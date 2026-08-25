"""Standalone MCP backend.

Everything above this file (generated wrappers, codegen, seam) is unchanged —
only this backend swaps. Auth uses the official `mcp` SDK OAuthClientProvider with a thin
FileTokenStorage that stores an absolute `expires_at` per token.

Pre-flight refresh: `get_tokens()` returns None for a near/expired access token
(see below). To stop that None from reaching the SDK — which would trigger a full
browser re-auth (authorization_code flow) instead of a silent refresh —
`_pre_flight_refresh()` renews the access token out-of-band (plain httpx, RFC 8414
discovery) before the session opens.

VERIFIED (2026-06-14, eval_preflight.py + mcp 1.27.2 source read): pre-flight IS
load-bearing — but the precise
reason is subtler than "the SDK can't refresh". The SDK's `async_auth_flow` DOES
have a silent `refresh_token`-grant branch (`if not is_token_valid() and
can_refresh_token(): _refresh_token()`). It is simply UNREACHABLE for a
fresh-process CLI: `_initialize()` loads tokens from storage but never calls
`update_token_expiry`, so `token_expiry_time` stays None and `is_token_valid()`
(`not token_expiry_time or now <= token_expiry_time`) reports ANY disk-loaded
access token as valid regardless of real expiry. The proactive branch never fires;
the stale token is sent blind → 401. On 401 the SDK runs `authorization_code`
(browser), NOT a refresh grant. Net: every fresh CLI invocation that finds an
expired access token would re-auth in a browser without pre-flight. Conclusion:
do NOT drop pre-flight. (The server supports refresh grants — the SDK just
never reaches the code that issues them at cold start.)

The `get_tokens` None-gate (line 125) is redundant but harmless: without pre-flight
both gate-ON and gate-OFF lead to browser re-auth on expired tokens. It short-
circuits one unnecessary 401 round-trip when pre-flight fails for other reasons.

VERSION-SENSITIVE: this is mcp 1.27.2 behavior (dep is bounded `<2`). If a future
SDK calls `update_token_expiry` inside `_initialize`, the cold-start gap closes and
the SDK's own proactive refresh fires — re-run eval_preflight.py and re-evaluate
whether pre-flight is still needed.

Unrelated: FastMCP is not a dependency. FastMCP issue #3425 (stale expires_in on
reload) is a FastMCP bug fixed in fastmcp 3.2.0; our FileTokenStorage stores
absolute `expires_at` so that class of bug cannot occur regardless.
"""

from __future__ import annotations

import ast
import asyncio
import errno as _errno
import json
import os
import re
import shlex
import stat
import sys
import time
import warnings
import webbrowser
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, OAuthRegistrationError, TokenStorage
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

DEFAULT_CREDS_PATH = Path.home() / ".mcpgen" / "credentials.json"
DEFAULT_CONFIG_PATH = Path.home() / ".mcpgen" / "config.json"

# Credential backend — selects where OAuth tokens are stored.
# Resolution order (first wins):
#   1. CLI --cred-backend flag (passed through to FileTokenStorage)
#   2. MCPGEN_CRED_BACKEND env var
#   3. ~/.mcpgen/config.json  "cred_backend" key
#   4. default: "file"
_CRED_BACKEND_ENV = "MCPGEN_CRED_BACKEND"
_VALID_BACKENDS: frozenset[str] = frozenset({"file", "keyring", "auto"})
_KEYRING_SERVICE: str = "mcpgen"
_KEYRING_USER: str = "credentials"


def _load_client_config(path: Path | None = None) -> dict:
    """Load ~/.mcpgen/config.json (or override path). Returns {} if absent/invalid."""
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_client_config(updates: dict, path: Path | None = None) -> None:
    """Merge *updates* into the client config file, creating it if absent.

    Reads the existing config (if any), overlays *updates*, then writes back
    atomically with 0600 permissions. Other keys in the config are preserved.
    """
    target = path or DEFAULT_CONFIG_PATH
    data = _load_client_config(target)
    data.update(updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")  # see FileTokenStorage._file_save
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(data, indent=2).encode())
    except BaseException:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    os.replace(tmp, target)


def resolve_cred_backend(cli_value: str | None = None) -> str:
    """Return the resolved credential backend name.

    Resolution order: CLI arg → MCPGEN_CRED_BACKEND env → config file → "file".
    Raises ValueError for unknown values at any level.
    """
    if cli_value is not None:
        if cli_value not in _VALID_BACKENDS:
            raise ValueError(f"Unknown cred backend {cli_value!r}. Valid choices: {sorted(_VALID_BACKENDS)}")
        return cli_value
    env = os.environ.get(_CRED_BACKEND_ENV)
    if env:
        if env not in _VALID_BACKENDS:
            raise ValueError(f"{_CRED_BACKEND_ENV}={env!r} unknown. Valid choices: {sorted(_VALID_BACKENDS)}")
        return env
    cfg = _load_client_config()
    backend = cfg.get("cred_backend")
    if backend:
        if backend not in _VALID_BACKENDS:
            raise ValueError(f"config cred_backend={backend!r} unknown. Valid choices: {sorted(_VALID_BACKENDS)}")
        return backend
    return "file"


def _detect_keyring() -> str:
    """Return 'keyring' if a working OS keyring backend is available, else 'file'."""
    try:
        import keyring as _kr

        ring = _kr.get_keyring()
        module = getattr(type(ring), "__module__", "") or ""
        if "fail" in module:
            return "file"
        return "keyring"
    except Exception:
        return "file"


# ── Raw keyring helpers (raise on error — no silent fallback) ────────────────


def _require_store(parsed: object, doc: str) -> dict:
    """*parsed* if it is a credential store, else raise as if the bytes had not parsed.

    A store is a JSON *object* keyed by server name. `[]`, `null`, `42` and a bare string
    all parse cleanly and are all equally not one — a hand-edit gone wrong reaches this
    door as often as it reaches the truncated-bytes one. Without the check they travel one
    more line and die inside `data.pop(...)` or `data.get(...)` as a raw `TypeError` /
    `AttributeError`, which is the traceback-with-no-way-out that `login()`'s quarantine
    exists to remove — arrived at through a different door.

    `json.JSONDecodeError` and not a new type: it is the exception every reader of this
    store already handles for "bytes on disk that are not a store", `login()`'s quarantine
    catches it unchanged, and a caller outside this module catching it today keeps working.
    The position is honest — the defect is the top-level value, at offset 0.
    """
    if not isinstance(parsed, dict):
        raise json.JSONDecodeError(f"credential store is not a JSON object (got {type(parsed).__name__})", doc, 0)
    return parsed


def _keyring_read_raw() -> dict:
    """Read all credentials from the OS keyring. Raises on any failure."""
    import keyring as _kr  # lazy — tests can monkeypatch sys.modules["keyring"]

    raw = _kr.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    return _require_store(json.loads(raw), raw) if raw else {}


def _keyring_write_raw(data: dict) -> None:
    """Write all credentials to the OS keyring. Raises on any failure."""
    import keyring as _kr

    _kr.set_password(_KEYRING_SERVICE, _KEYRING_USER, json.dumps(data, indent=2))


def _keyring_clear_raw() -> None:
    """Delete the credentials entry from the OS keyring (no-op if absent).

    Only suppresses PasswordDeleteError (entry not found). All other failures
    — locked keychain, access denied, backend error — propagate so callers
    can surface them rather than falsely reporting deletion success.
    """
    import keyring as _kr

    try:
        _kr.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
    except _kr.errors.PasswordDeleteError:
        pass  # already absent


# Named HTTP+OAuth servers are loaded from a user config — never hardcoded, so no
# org-specific endpoints land in this repo. Search order:
#   1. $MCPGEN_SERVERS                        (explicit path)
#   2. ~/.mcpgen/servers.json                 ({"name": "url", ...} or mcpServers)
#   3. ./.mcp.json                            (Claude Code format: {"mcpServers": {...}})
# Any name not found here is treated as a raw URL (no auth). See servers.example.json.
_SERVERS_CONFIG_ENV = "MCPGEN_SERVERS"
_SERVERS_SEARCH = [
    Path.home() / ".mcpgen" / "servers.json",
    Path.cwd() / ".mcp.json",
]
_servers_cache: dict[str, str] | None = None
# Per-server OAuth client_name overrides, keyed by server name. Populated alongside
# _servers_cache by servers(). A server with no override in config is absent here and
# falls back to the default template (see _resolve_client_name()).
_client_names_cache: dict[str, str] = {}
# Stdio server specs keyed by server name. Each value is a dict with keys "command"
# (str), "args" (list[str]), and "env" (dict[str, str] | None). Populated alongside
# _servers_cache by servers() from config entries that have "command" but no "url".
_stdio_cache: dict[str, dict] = {}
# Static HTTP headers keyed by server name. Populated alongside _servers_cache by
# servers() from config entries that have both "url" and "headers". Values in "headers"
# have ``${VAR}`` references expanded at parse time (same as stdio "env").
_headers_cache: dict[str, dict[str, str]] = {}
# Declared transport type keyed by server name, e.g. {"legacy": "sse"}. Populated
# alongside _servers_cache by servers(). Only entries with an explicit "type" are
# present. Used to refuse SSE up front — this client has no SSE transport adapter,
# and without the guard an SSE URL silently takes the Streamable HTTP path.
_types_cache: dict[str, str] = {}


def _filter_str_dict(raw: dict, *, require_nonempty_key: bool = False) -> dict[str, str]:
    """Filter a raw config dict to {str: str} keeping only scalar (non-bool) values.

    Values have ``${VAR}`` references expanded against the host environment.
    Entries with non-scalar values are dropped silently.  When
    ``require_nonempty_key`` is True, entries with empty-string keys are also
    dropped (used for HTTP headers where RFC 9110 forbids empty field names).
    """
    result = {}
    for k, v in raw.items():
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            sk = str(k)
            if require_nonempty_key and not sk:
                continue
            result[sk] = os.path.expandvars(str(v))
    return result


def _parse_servers(
    raw: dict,
) -> tuple[dict[str, str], dict[str, str], dict[str, dict], dict[str, dict[str, str]]]:
    """Parse config into ({name: url}, {name: client_name}, {name: stdio_spec}, {name: headers}).

    Accept {"name": "url"} or Claude Code {"mcpServers": {"name": {"url": ...}}}.
    The dict form may carry an optional "clientName" (or "client_name" alias) that
    overrides the OAuth client_name sent at Dynamic Client Registration.

    Stdio entries (those with "command" but no "url") are collected in the third
    return dict.  Each stdio_spec has keys "command" (str), "args" (list[str]), and
    "env" (dict[str, str] | None).  Values in "env" have ``${VAR}`` references
    expanded against the host environment so secrets stored as env-var references
    resolve at parse time.

    HTTP entries with a "headers" dict have those headers collected in the fourth
    return dict with ``${VAR}`` references expanded, enabling static header auth
    (e.g. ``Authorization: Bearer ${GITHUB_PAT}``) without ``--bearer``.
    """
    block = raw.get("mcpServers", raw)
    urls: dict[str, str] = {}
    names: dict[str, str] = {}
    cmds: dict[str, dict] = {}
    hdrs: dict[str, dict[str, str]] = {}
    for name, val in block.items():
        if isinstance(val, str):
            urls[name] = val
        elif isinstance(val, dict) and val.get("url"):
            urls[name] = val["url"]
            override = val.get("clientName") or val.get("client_name")
            if override:
                names[name] = override
            raw_headers = val.get("headers") or {}
            if isinstance(raw_headers, dict):
                parsed_h = _filter_str_dict(raw_headers, require_nonempty_key=True)
                if parsed_h:
                    hdrs[name] = parsed_h
        elif isinstance(val, dict) and val.get("command"):
            raw_args = val.get("args") or []
            args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
            raw_env = val.get("env") or {}
            env: dict[str, str] | None = None
            if isinstance(raw_env, dict):
                parsed_e = _filter_str_dict(raw_env)
                if parsed_e:
                    env = parsed_e
            cmds[name] = {"command": str(val["command"]), "args": args, "env": env}
    return urls, names, cmds, hdrs


def _parse_server_types(raw: dict) -> dict[str, str]:
    """Return {name: declared transport type} for entries carrying an explicit "type".

    Kept separate from _parse_servers() so that function's 4-tuple contract, which
    several callers unpack positionally, stays stable.
    """
    block = raw.get("mcpServers", raw)
    types: dict[str, str] = {}
    for name, val in block.items():
        if isinstance(val, dict):
            declared = val.get("type")
            if isinstance(declared, str) and declared:
                types[name] = declared.lower()
    return types


def servers(*, refresh: bool = False, config_path: str | Path | None = None) -> dict[str, str]:
    """Return the {name: url} registry loaded from user config (cached).

    config_path: if given, read that file exclusively (authoritative — no env or
    search-order fallback) and always fresh, bypassing the cache.  A missing or
    unparseable explicit config raises rather than silently returning an empty dict.
    """
    global _servers_cache, _client_names_cache, _stdio_cache, _headers_cache, _types_cache
    if config_path is None and _servers_cache is not None and not refresh:
        return _servers_cache
    if config_path is not None:
        # Authoritative path — fail fast, no search-order fallback.
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"config not found: {config_path}")
        try:
            raw = json.loads(path.read_text())
            _servers_cache, _client_names_cache, _stdio_cache, _headers_cache = _parse_servers(raw)
            _types_cache = _parse_server_types(raw)
        except (json.JSONDecodeError, OSError, AttributeError) as e:
            raise ValueError(f"failed to parse config {path}: {e}") from e
        return _servers_cache
    candidates: list[Path] = []
    if os.environ.get(_SERVERS_CONFIG_ENV):
        candidates.append(Path(os.environ[_SERVERS_CONFIG_ENV]))
    candidates += _SERVERS_SEARCH
    for path in candidates:
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                _servers_cache, _client_names_cache, _stdio_cache, _headers_cache = _parse_servers(raw)
                _types_cache = _parse_server_types(raw)
                return _servers_cache
            except (json.JSONDecodeError, OSError, AttributeError):
                continue
    _servers_cache, _client_names_cache, _stdio_cache, _headers_cache = {}, {}, {}, {}
    _types_cache = {}
    return _servers_cache


def _resolve_client_name(server_name: str) -> str:
    """OAuth client_name for a server: config override, else default template."""
    if _servers_cache is None:
        servers()
    return _client_names_cache.get(server_name) or f"mcpgen ({server_name})"


def _client_metadata(server_name: str, callback_uri: str, client_name: str | None = None) -> OAuthClientMetadata:
    """Dynamic-registration metadata (RFC 7591). Shared by both OAuth entry points.

    `token_endpoint_auth_method="none"` is load-bearing. Omit it and the AS applies
    the RFC 7591 §2 default of `client_secret_basic` and issues a `client_secret`;
    the SDK then sends an `Authorization: Basic` header *and* `client_id` in the form
    body — two client authentication methods in one request, which servers that
    enforce RFC 6749 §2.3 reject with `400 invalid_request`. Registering as a public
    client (RFC 8252 §8.4) is the correct posture for a distributed CLI anyway, and
    the SDK's PKCE (S256, unconditional) is what actually secures the flow.
    """
    return OAuthClientMetadata(
        client_name=client_name or _resolve_client_name(server_name),
        redirect_uris=[callback_uri],  # type: ignore[list-item]  # Pydantic coerces str→AnyUrl
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


def _explain_registration_error(exc: OAuthRegistrationError) -> OAuthRegistrationError:
    """Annotate `invalid_client_metadata` with its most likely cause, credential removed.

    An AS that does not support public clients MUST reject our registration
    (RFC 7591 §2) rather than downgrade it. No such server is known, so rather than
    carry a speculative override flag we make the failure legible if one turns up.

    Every registration error passes through here, which is why the redaction lives here
    rather than at the raise site. The SDK reports a 2xx registration body that fails
    validation as `OAuthRegistrationError(f"Invalid registration response: {pydantic}")`,
    and an RFC 7591 registration response carries `client_secret` — the member that
    outlives every token in `_SECRET_MEMBERS`. The pydantic text quotes it back as a repr,
    which is the spelling `_SECRET_REPR_RE` exists for.
    """
    text = _redact_secret_text(str(exc))
    if "invalid_client_metadata" not in text:
        return OAuthRegistrationError(text) if text != str(exc) else exc
    return OAuthRegistrationError(
        f"{text}\n\n"
        "Likely cause: mcpgen registers as a public client "
        "(token_endpoint_auth_method=none), and this authorization server appears to "
        "require a client_secret. Please report this at "
        "https://github.com/svd/mcp-client-kit/issues with the message above."
    )


# Treat a cached token as expired this many seconds before its real expiry.
_MARGIN = 120

# Sub-500 statuses from the token endpoint that mean "ask again later", not "the
# grant is dead":
#   408 — the request was never processed. It comes from the proxy layer, not the
#         authorization server, and RFC 9110 §15.5.9 says to repeat it.
#   429 — rate limited. The next attempt is expected to succeed.
_RETRYABLE_REFRESH_STATUS = frozenset({408, 429})

# The RFC 6749 §5.2 error codes that mean the cached credential itself is gone, so
# a browser round is the fix:
#   invalid_grant       — the refresh token is expired, revoked, or was never ours.
#   invalid_client      — the client registration was rejected.
#   unauthorized_client — this client may not use the refresh_token grant.
# The last two are about the registration rather than the token, and `login()` drops
# the cached `client_info` before it runs, so the SDK registers a new client: it does
# replace what failed. A server whose *policy* forbids refresh for this client class
# will answer the same way to the new registration, so that case is not fixed — but it
# still logs in, and the alternative is worse. Automation and headless callers get no
# browser prompt at all, so leaving a condition that re-registration can repair out of
# this set turns it into a permanent hard failure recoverable only by a human running
# a command. One avoidable prompt per expiry beats no route back.
#
# The remaining §5.2 codes (invalid_request, unsupported_grant_type, invalid_scope)
# fault the *request*: the grant is untouched, and logging in again resends it.
_DEAD_GRANT_ERRORS = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})

# Error codes that name a temporary condition rather than a permanent one. §5.2 does
# not define these — they belong to the authorization *endpoint* in §4.1.2.1 — but
# servers reuse them at the token endpoint, and both mean "the same request may work
# later". RFC 8628's `slow_down` is deliberately absent: it is a device-flow polling
# code with no meaning for a refresh_token grant, so accepting it here would only
# widen the set with a case that cannot legitimately arrive.
_RETRYABLE_REFRESH_ERRORS = frozenset({"temporarily_unavailable", "server_error"})

# How long the interactive login waits for the browser to hit the local callback
# server. Some authorization servers drop the user on cancel without an error
# redirect, so the callback simply never arrives — bound the wait rather than
# hang forever. Generous: it has to cover reading a consent screen and an MFA
# prompt. Headless login is not bounded — a human pasting a URL may take any
# amount of time.
_CALLBACK_TIMEOUT = 300


class ReauthenticationRequired(Exception):
    """Tokens absent or the grant is dead. Run: mcpgen login <server>"""


class LoginWontHelp(Exception):
    """Base for auth failures another browser round cannot fix.

    The counterpart to ``ReauthenticationRequired``, and deliberately not named for
    a cause: what these failures have in common — and the only thing a caller can
    act on — is that sending the user through the browser again changes nothing.
    Batch callers should catch this and abort rather than re-prompting once per
    item; that distinction is why the type exists. Catch a subclass only when the
    difference between them matters.
    """


class PostLoginCheckFailed(LoginWontHelp):
    """The OAuth flow finished and the token is cached, but the check after it failed.

    The token was issued, which says nothing about whether the resource server
    accepted it. A 502 from the origin, a post-login 401 over scope or audience,
    and an MCP-level error from ``list_tools()`` all raise this.
    """


class TokenRefreshUnavailable(LoginWontHelp):
    """The cached grant was not renewed, and no browser round would have changed that.

    Everything the token endpoint can answer that is *not* the authorization server
    naming a dead credential lands here: a transport error, a 5xx, a retryable
    status or error code, a block page from whatever stands in front of the server,
    and an error code that faults the request rather than the grant. What they share
    is that the refresh token is untouched — so the browser has nothing to replace,
    and re-running the flow only repeats the same failure.

    The message says which one it was, and whether retrying is the move.
    """


class FileTokenStorage(TokenStorage):
    """OAuth token + client info store, keyed by server name.

    Backend selection via the ``backend`` argument (already resolved by
    ``resolve_cred_backend()`` at construction site):

    - ``"file"``    (default) — hardened plaintext JSON at *credentials_path*
                    (``chmod 0600`` file, ``0700`` dir, atomic write via tmp file).
    - ``"keyring"`` — OS keyring (macOS Keychain / Windows Credential Locker /
                    Linux SecretService). Falls back to the hardened file if no
                    working backend is available, with a warning.
    - ``"auto"``    — keyring if ``_detect_keyring()`` finds a working backend,
                    else file silently.

    The public ``_load()`` / ``_save()`` seam routes to the active backend so
    ``_pre_flight_refresh`` and ``login()`` need no changes.
    """

    def __init__(
        self,
        server_name: str,
        credentials_path: Path = DEFAULT_CREDS_PATH,
        backend: str = "file",
    ) -> None:
        self._key = server_name
        self._path = credentials_path
        self._backend = _detect_keyring() if backend == "auto" else backend

    # ── File backend ──────────────────────────────────────────────────────────

    def _file_load(self) -> dict:
        if not self._path.exists():
            return {}
        mode = stat.S_IMODE(os.stat(self._path).st_mode)
        if mode != 0o600:
            os.chmod(self._path, 0o600)
            warnings.warn(
                f"[mcpgen] {self._path} had permissions {oct(mode)}; fixed to 0600.",
                stacklevel=3,
            )
        # Explicit UTF-8: `_file_save` writes `json.dumps(...).encode()`, which is UTF-8, so
        # a locale-dependent read would round-trip a non-ASCII server name wrong on Windows.
        raw = self._path.read_text(encoding="utf-8")
        return _require_store(json.loads(raw), raw)

    def _file_save(self, data: dict) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
        # Pid-unique staging name, same convention as cli._atomic_write_text: a fixed
        # ".tmp" lets two mcpgen processes clobber each other's partial write. It does
        # not make concurrent writes safe — the last os.replace still wins, so one
        # process's credential update can be lost. That wants a lock or a
        # compare-and-set; this only stops the two from corrupting each other's file.
        # The trade it does make: the fixed name left at most one stale file behind, while
        # this leaves one per SIGKILL. Both write sites clean up on a raised exception, so
        # only an unhandled signal strands one, and a 0600 partial JSON next to the store
        # is inert — worth less than the corruption it prevents.
        tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(data, indent=2).encode())
        except BaseException:
            # Close and remove the partial tmp so it doesn't accumulate or leak.
            os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.close(fd)
        os.replace(tmp, self._path)

    # ── Keyring backend ───────────────────────────────────────────────────────

    def _keyring_load(self) -> dict:
        try:
            return _keyring_read_raw()
        except Exception as exc:
            self._warn_keyring_fallback(str(exc))
            return self._file_load()

    def _keyring_save(self, data: dict) -> None:
        try:
            _keyring_write_raw(data)
        except Exception as exc:
            self._warn_keyring_fallback(str(exc))
            self._file_save(data)

    def _warn_keyring_fallback(self, reason: str) -> None:
        """Warn and permanently downgrade to the file backend for this instance.

        Mutation is intentional: after one keyring failure all subsequent
        _load/_save calls use the hardened file, avoiding repeated failures and
        warnings within the same process lifetime.
        """
        warnings.warn(
            f"[mcpgen] keyring unusable ({reason}); falling back to hardened file at {self._path}.",
            stacklevel=3,
        )
        self._backend = "file"

    # ── Dispatcher (public seam used by _pre_flight_refresh and login) ────────

    def _load(self) -> dict:
        if self._backend == "keyring":
            return self._keyring_load()
        return self._file_load()

    def _save(self, data: dict) -> None:
        if self._backend == "keyring":
            self._keyring_save(data)
        else:
            self._file_save(data)

    # ── TokenStorage protocol ─────────────────────────────────────────────────

    async def get_tokens(self) -> OAuthToken | None:
        data = self._load()
        raw = data.get(self._key, {}).get("tokens")
        if raw is None:
            return None
        expires_at = raw.get("expires_at")
        if expires_at is not None and time.time() >= expires_at - _MARGIN:
            # Pre-flight refresh should have run; if still here, treat as absent.
            return None
        return OAuthToken(**raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        serialized = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in is not None:
            serialized["expires_at"] = int(time.time()) + int(tokens.expires_in)
        data.setdefault(self._key, {})["tokens"] = serialized
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._load()
        raw = data.get(self._key, {}).get("client_info")
        if raw is None:
            return None
        return OAuthClientInformationFull(**raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data.setdefault(self._key, {})["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)


# ── Backend-agnostic migration helpers ──────────────────────────────────────


def _read_backend(backend: str, credentials_path: Path) -> dict:
    """Read the full credentials dict from *backend* (raises on keyring failure)."""
    if backend == "keyring":
        return _keyring_read_raw()
    return FileTokenStorage("_migrate", credentials_path, backend="file")._file_load()


def _write_backend(backend: str, credentials_path: Path, data: dict) -> None:
    """Write the full credentials dict to *backend* (raises on keyring failure)."""
    if backend == "keyring":
        _keyring_write_raw(data)
    else:
        FileTokenStorage("_migrate", credentials_path, backend="file")._file_save(data)


def _clear_backend(backend: str, credentials_path: Path) -> None:
    """Remove all credentials from *backend*."""
    if backend == "keyring":
        _keyring_clear_raw()
    else:
        credentials_path.unlink(missing_ok=True)


def migrate_creds(
    from_backend: str,
    to_backend: str,
    *,
    servers: list[str] | None = None,
    credentials_path: Path = DEFAULT_CREDS_PATH,
    purge: bool = False,
    set_default: bool = False,
    config_path: Path | None = None,
) -> dict:
    """Copy stored credentials from one backend to another.

    Parameters
    ----------
    from_backend:
        Source backend — ``"file"`` or ``"keyring"``.
    to_backend:
        Target backend — ``"file"`` or ``"keyring"``.
    servers:
        Optional list of server names to migrate. ``None`` migrates all.
        Raises ``ValueError`` if a requested name is absent in the source.
    credentials_path:
        Path to the file backend's credentials JSON (default:
        ``~/.mcpgen/credentials.json``).
    purge:
        When ``True``, remove only the migrated entries from the source backend
        after a verified write. When ``False`` (default), the source is kept.
    set_default:
        When ``True``, write ``cred_backend=<to_backend>`` into the client
        config file so future commands default to the new backend.
    config_path:
        Override path for the client config (default:
        ``~/.mcpgen/config.json``).

    Returns
    -------
    dict
        ``{"from": str, "to": str, "migrated": int, "overwritten": int,
           "purged": bool, "set_default": bool}``
    """
    # Validate
    concrete = {"file", "keyring"}
    for label, val in [("from_backend", from_backend), ("to_backend", to_backend)]:
        resolved = _detect_keyring() if val == "auto" else val
        if resolved not in concrete:
            raise ValueError(f"{label}={val!r} is not a concrete backend. Valid choices: {sorted(concrete)}")
    from_backend = _detect_keyring() if from_backend == "auto" else from_backend
    to_backend = _detect_keyring() if to_backend == "auto" else to_backend

    if from_backend == to_backend:
        raise ValueError(f"from_backend and to_backend are both {from_backend!r}; nothing to migrate.")

    # Read source
    source_all = _read_backend(from_backend, credentials_path)

    # Filter to requested servers
    if servers is not None:
        missing = [s for s in servers if s not in source_all]
        if missing:
            raise ValueError(
                f"Requested server(s) not found in {from_backend!r} backend: " + ", ".join(repr(s) for s in missing)
            )
        source_subset = {k: source_all[k] for k in servers}
    else:
        source_subset = source_all

    if not source_subset:
        return {
            "from": from_backend,
            "to": to_backend,
            "migrated": 0,
            "overwritten": 0,
            "purged": False,
            "set_default": False,
        }

    # Merge into target (source wins on collision)
    target = _read_backend(to_backend, credentials_path)
    overwritten = sum(1 for k in source_subset if k in target)
    merged = {**target, **source_subset}
    _write_backend(to_backend, credentials_path, merged)

    # Verify write
    verified = _read_backend(to_backend, credentials_path)
    missing_after = [k for k in source_subset if k not in verified]
    if missing_after:
        raise RuntimeError(
            f"Migration verification failed: the following server(s) are absent from "
            f"the {to_backend!r} backend after write: {', '.join(missing_after)}"
        )

    # Optional purge (remove only the migrated keys from source)
    did_purge = False
    if purge:
        source_remaining = _read_backend(from_backend, credentials_path)
        for k in source_subset:
            source_remaining.pop(k, None)
        if source_remaining:
            _write_backend(from_backend, credentials_path, source_remaining)
        else:
            _clear_backend(from_backend, credentials_path)
        did_purge = True

    # Optional config default
    did_set_default = False
    if set_default:
        _save_client_config({"cred_backend": to_backend}, config_path)
        did_set_default = True

    return {
        "from": from_backend,
        "to": to_backend,
        "migrated": len(source_subset),
        "overwritten": overwritten,
        "purged": did_purge,
        "set_default": did_set_default,
    }


def list_creds(
    *,
    backend: str | None = None,
    credentials_path: Path = DEFAULT_CREDS_PATH,
    expired_only: bool = False,
) -> list[dict]:
    """List stored credentials.

    Returns a list of dicts — one per server, sorted by name — with the keys:

    - ``name``:              server name
    - ``expires_at``:        absolute Unix epoch (int) or ``None`` if no expiry
    - ``expired``:           ``True`` when ``time.time() >= expires_at - _MARGIN``
                             (same rule as :py:meth:`FileTokenStorage.get_tokens`)
    - ``has_refresh_token``: ``True`` when a refresh_token is stored

    Parameters
    ----------
    backend:
        Credential backend to read.  Resolved via :func:`resolve_cred_backend`
        when ``None`` (env → config → ``"file"``).
    credentials_path:
        Path to the file backend (default ``~/.mcpgen/credentials.json``).
    expired_only:
        When ``True``, omit entries that are valid or have no expiry information.
    """
    resolved = resolve_cred_backend(backend)
    resolved = _detect_keyring() if resolved == "auto" else resolved
    data = _read_backend(resolved, credentials_path)
    now = time.time()
    out = []
    for name, entry in sorted(data.items()):
        tok = (entry or {}).get("tokens") or {}
        exp = tok.get("expires_at")
        expired = exp is not None and now >= exp - _MARGIN
        if expired_only and not expired:
            continue
        out.append(
            {
                "name": name,
                "expires_at": exp,
                "expired": expired,
                "has_refresh_token": bool(tok.get("refresh_token")),
            }
        )
    return out


def delete_cred(
    name: str,
    *,
    backend: str | None = None,
    credentials_path: Path = DEFAULT_CREDS_PATH,
) -> bool:
    """Delete the stored credential for *name*.

    Returns ``True`` if the entry existed (and was removed), ``False`` if it
    was not found. When the removed entry was the last one, the whole backend
    store is cleared (file unlinked / keyring key cleared) to avoid leaving an
    empty JSON object.

    Parameters
    ----------
    name:
        Server name whose credential to delete.
    backend:
        Credential backend to write.  Resolved via :func:`resolve_cred_backend`
        when ``None``.
    credentials_path:
        Path to the file backend (default ``~/.mcpgen/credentials.json``).
    """
    resolved = resolve_cred_backend(backend)
    resolved = _detect_keyring() if resolved == "auto" else resolved
    data = _read_backend(resolved, credentials_path)
    if name not in data:
        return False
    data.pop(name)
    if data:
        _write_backend(resolved, credentials_path, data)
    else:
        _clear_backend(resolved, credentials_path)
    return True


_SECRET_MEMBERS = frozenset({"access_token", "refresh_token", "id_token", "client_secret"})
"""Credential-bearing members that must never reach a log line: the token-response members
of RFC 6749 §5.1 and §4.1.4, plus `client_secret` — a registration-response member (RFC 7591
§3.2.1) that gateways also echo back out of a failed token request. It outlives every token
in the set, since a dynamic client's secret does not expire. Redacting it costs no diagnostic
value: what a reader needs from it is that it was *sent*, and `"<redacted>"` still says so.

Matching ignores case *and* the separator between words. §5.1 mandates the lowercase
snake_case spelling, but it binds the *authorization server* — and the bodies that reach
`_body_excerpt` are exactly the ones where something else answered: a WAF, an API gateway, a
vendor wrapper re-serialising through its own naming convention. Holding a non-compliant
responder to the compliant spelling is how the credential gets printed, and the single most
common thing such a wrapper does to a member name is re-case it: a JSON serializer left on
its defaults emits `accessToken`, and a kebab-case house style emits `access-token`. Folding
case without folding the separator covers the least likely half of the same failure.

Nothing outside a credential is plausibly named any casing or separator spelling of these
four, so the fold costs no diagnostic. The near misses do not collide: `access_token_expires_in`
normalises to a longer string and keeps its value."""

_SECRET_MEMBERS_NORM = frozenset(m.replace("_", "") for m in _SECRET_MEMBERS)
"""``_SECRET_MEMBERS`` with the separators removed, for key lookups after normalisation."""

# `access[-_]?token|…` — one alternation, built once, shared by both regexes below so the
# unparsed-body path can never fall a generation behind the structured one. The optional
# separator class is what reaches `accessToken` and `access-token`; `re.I` does the rest.
_SECRET_MEMBERS_RE_SRC = "|".join(m.replace("_", "[-_]?") for m in sorted(_SECRET_MEMBERS))

_SECRET_FORM_RE = re.compile(rf"\b({_SECRET_MEMBERS_RE_SRC})=[^&\s\"']*", re.I)
"""The same members in a form-encoded body. GitHub's token endpoint answers that way by
default, so the JSON path below is not the only one that can carry a live credential."""

_SECRET_JSON_RE = re.compile(rf'"({_SECRET_MEMBERS_RE_SRC})\\?"\s*:\s*\\?"(?:[^"\\]|\\.)*\\?(?:"|\Z)', re.I)
"""The same members in text the structured pass cannot reach, in two distinct senses.

The obvious one is a body `httpx` refuses to parse: a response truncated mid-token by a
proxy is still a body with a live credential in it, and it fails `resp.json()` precisely
because it was cut short. The second is subtler and is not confined to malformed input —
`_redact_secrets` matches on dict *keys*, so a body that parses perfectly but carries the
token inside a *string* (`{"error_description": "{\\"access_token\\": \\"…\\"}"}`, a gateway
echoing an upstream body) has no secret key to find, survives the structured pass untouched,
and reaches this regex as the only remaining defense. Both regexes therefore run
unconditionally, after the structured pass.

The value ends at a closing quote *or at the end of the text*, because the worst case is the
value that was cut mid-token: requiring the closing quote would let exactly the most-truncated
body through. `(?:[^"\\]|\\.)*` steps over an escaped quote inside the value rather than
stopping at it, and the three `\\?` allow the escaped-quote spelling of the same shape — the
nested-in-a-string case above, plus a value truncated on a lone trailing backslash.

The over-match is deliberate and worth naming: inside an escaped blob every quote is escaped,
so the match runs from the secret to the next *unescaped* quote and takes any siblings after
it, leaving the excerpt structurally unterminated there. Everything before the secret — the
`error` code, the head of `error_description` — survives, which is what makes the excerpt
worth printing, and the loss is bounded to the tail of a string that held a live credential.
A lazier or length-bounded pattern would trade that for a leak, since whatever falls outside
the shorter match is the rest of the token."""

_SECRET_REPR_RE = re.compile(rf"'?\b({_SECRET_MEMBERS_RE_SRC})'?\s*[:=]\s*'(?:[^'\\]|\\.)*(?:'|\Z)", re.I)
"""The same members in a *Python* repr, which is neither of the two shapes above.

This is the spelling an exception message carries, not a response body. The MCP SDK raises
`OAuthTokenError(f"Invalid token response: {e}")` and `OAuthRegistrationError(...)` over a
pydantic `ValidationError`, and pydantic quotes the rejected `input_value` — a dict repr,
single-quoted: `input_value={'accessToken': 'ya29…', 'refresh_token': '1//…'}`. Both regexes
above miss it, `_SECRET_JSON_RE` because it needs a double quote and `_SECRET_FORM_RE`
because it needs `=`, so before this pattern a login whose post-check failed printed the
credential to stderr in full.

`[:=]` covers the dict spelling and the keyword/attribute spelling (`access_token='ya29…'`)
in one pattern. That second spelling is why this must run *before* `_SECRET_FORM_RE`:
that pattern stops at the quote and would leave `access_token=<redacted>'SECRET'` — a
substitution that reads as redacted and is not.

What keeps it off prose is the value anchor: the member name must be followed by `:` or `=`
and then an *opening single quote*. `error_description='access_token was rejected'` does not
match — after the member comes a space — so a message that merely names one of these members
survives intact. The residual over-match is named for the same reason `_SECRET_JSON_RE` names
its own: `invalid 'access_token': 'expected a string'` is eaten, because it is indistinguishable
in shape from a key carrying the credential. One quoted phrase is the bounded cost; a live
token is the unbounded one. The `\\Z` terminator matters for a repr that arrives cut short;
pydantic's own frames are redacted wholesale upstream by `_PYDANTIC_INPUT_VALUE_RE`, so what
reaches this pattern truncated is a repr with no `input_value=` frame around it.

Not matched: a value Python repr'd with double quotes because it contains an apostrophe
(`{'access_token': "ya'29"}`). Tokens are base64url or JWT material and cannot contain `'`,
so that spelling cannot carry one of these four members' real values."""


_PYDANTIC_INPUT_VALUE_RE = re.compile(r"\binput_value=.*?(?=,\s*input_type=|$)", re.M)
"""Pydantic's quoted-input frame, dropped whole rather than scrubbed member by member.

Member-wise redaction is unreliable on this text *by construction*, and the reason is not
exotic: pydantic truncates the quoted repr in the middle, with a literal `...`, and the cut
lands mid-key as readily as mid-value. `{'accessToken': 'SECRET1'...efreshToken': 'SECRET2'}`
is real output — the second key lost its `r`, so no member pattern can match it, and its
value is intact. Where the cut lands is a function of the total repr length, which the
*server* controls, so any key-anchored fix closes one offset and leaves the class open. A
suffix match fails the same way: one character further and the survivor reads `oken':`.

Keying on `input_value=` is what makes this independent of the cut. Pydantic emits the
marker and terminates the frame with `, input_type=`, and it truncates the repr inside the
frame, never the frame itself. The lazy `.*?` therefore does not care what brace soup the
value contains — a nested dict, a list, a bare int, a plain string all end at the same
lookahead. `$` under `re.M` bounds a message some upstream wrapper had already cut before it
reached us; it is not there for `_DESCRIBE_LIMIT`, since both consumers redact before they
cap.

It also reaches a spelling no key pattern can: a *field-level* error on a secret field prints
`input_value='ya29…'` with the field name on the line above and no key beside the value at all.

The cost is the frame's non-secret siblings — `token_type`, `expires_in`, `scope`. That is
affordable because every pydantic validation this module can reach is a token response or an
RFC 7591 registration response: the two bodies it already declines to print raw. What carries
the diagnostic survives — the field name above, `[type=missing]`, `input_type=dict`, the docs
URL — which is the same line `_body_excerpt` draws between the credential and the reason.

A value containing the literal `, input_type=` would end the match early; a token cannot,
being base64url or JWT material, and the member patterns still run afterwards over whatever
tail is left."""


def _redact_secret_text(text: str, *, pydantic_frames: bool = True) -> str:
    """*text* with every credential-shaped member removed, in all four spellings.

    One helper so the two consumers — `_body_excerpt` for response bodies, `_describe` for
    exception messages — cannot drift apart, and so a fifth spelling added later reaches
    both. Order matters and is not alphabetical, twice over. The pydantic frame goes first,
    so the member patterns only ever see what it left behind and act as a backstop rather
    than contending for the same span. The repr pattern then precedes `_SECRET_FORM_RE`,
    which would otherwise half-match the `key='value'` spelling and stop at the quote,
    leaving the credential sitting next to the word `<redacted>`.

    `pydantic_frames=False` for text that is not one of *our* exception messages. The frame
    pattern's cost is argued on the corpus `_describe` sees — messages this module produced
    from a token or registration response. A raw server body is a different corpus: it never
    carries a frame this module made, so the pattern has no work to do there, and an
    authorization server that puts `str(validation_error)` in its own `error_description`
    would lose the rest of that line for nothing. The member patterns still cover the body,
    which is what was ever protecting it.
    """
    if pydantic_frames:
        text = _PYDANTIC_INPUT_VALUE_RE.sub("input_value=<redacted>", text)
    text = _SECRET_REPR_RE.sub(r"'\1': '<redacted>'", text)
    text = _SECRET_FORM_RE.sub(r"\1=<redacted>", text)
    return _SECRET_JSON_RE.sub(r'"\1": "<redacted>"', text)


def _norm_member(key: str) -> str:
    """*key* folded to the spelling ``_SECRET_MEMBERS_NORM`` is keyed by.

    Case and word separator both go, for the reason given at `_SECRET_MEMBERS`: the
    responders that reach here are the ones re-serialising through their own naming
    convention, and `accessToken` is what that convention most often produces.
    """
    return key.lower().replace("-", "").replace("_", "")


def _redact_secrets(value: object) -> object:
    """*value* with every ``_SECRET_MEMBERS`` member replaced, at any nesting depth.

    Scanning only the top level would miss the wrapped shape, which is not hypothetical:
    Slack answers `{"ok": true, "authed_user": {"access_token": …}}`, and that endpoint is
    the one `_pre_flight_refresh` singles out for in-band failure handling. A top-level
    scan finds no secret member there and hands the whole live token to the caller.
    """
    if isinstance(value, dict):
        return {
            k: "<redacted>" if isinstance(k, str) and _norm_member(k) in _SECRET_MEMBERS_NORM else _redact_secrets(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


def _body_excerpt(resp: httpx.Response) -> str:
    """*resp*'s body, with any credential in it removed, capped at ``_DESCRIBE_LIMIT``.

    A token endpoint's body is the one place a live credential is *expected*, so echoing
    it verbatim into an error is a credential leak, not a diagnostic. It matters most on
    the path that looks least dangerous: a 200 that fails to validate as an ``OAuthToken``
    (a `token_type` outside the `Bearer` literal, a non-integer `expires_in`) is still a
    full token response, and the message naming that failure is printed to stderr by
    `cli.py` and by the generated runner — into CI logs, in both cases.

    Redaction runs before truncation, so a secret cannot survive by sitting past the cap.
    The escape hatch is deliberate: everything *except* the credential is kept, because
    the surrounding members (`error`, `error_description`, an HTML block page) are what
    make the message worth printing at all.
    """
    text = resp.text
    try:
        parsed = resp.json()
    except Exception:  # noqa: BLE001 — a body that is not JSON is handled by the regexes below
        parsed = None
    if parsed is not None:
        try:
            scrubbed = _redact_secrets(parsed)
            # Only re-serialise when something was actually removed: a clean body is worth
            # more to the reader in the server's own formatting than in `json.dumps`'.
            if scrubbed != parsed:
                text = json.dumps(scrubbed)
        except RecursionError:
            # `_redact_secrets` costs two frames per level (the call and its comprehension),
            # so it gives out at roughly half the nesting `json.loads` accepts — leaving a
            # window where a body the parser took blows up the code *reporting* on it. The
            # regexes below are iterative and do not care about depth, so falling through to
            # them keeps the redaction rather than replacing the diagnostic with a traceback.
            pass
    text = _redact_secret_text(text, pydantic_frames=False)
    if len(text) > _DESCRIBE_LIMIT:
        return text[:_DESCRIBE_LIMIT] + "…"
    return text


def _oauth_error_code(resp: httpx.Response) -> str | None:
    """The RFC 6749 §5.2 ``error`` code in *resp*, or None if it does not carry one.

    §5.2 defines the shape as a JSON object with a string ``error`` member, and it
    identifies both the *speaker* and *what it objected to* — the only thing that
    separates a dead credential from a rejected request. A proxy or WAF standing in
    front of an authorization server usually answers with HTML, so a body naming one
    of the RFC codes is strong evidence the server itself replied. Not proof: a
    JSON-speaking API gateway can return an ``error`` member of its own. The cost of
    that collision is bounded — a gateway string will not match one of the three
    ``_DEAD_GRANT_ERRORS``, so it lands in the request-faulted branch and produces a
    slightly misleading message, never a wrong decision about the browser.

    Real token endpoints answer ``application/x-www-form-urlencoded`` unless content
    negotiation succeeds — GitHub's does by default — so a body *labelled* form-encoded
    is read too. Asking for JSON (`Accept`, at the call site) is a request, not a
    guarantee, and a server that ignores it was otherwise reporting every rejection,
    `invalid_grant` included, as a body with no error code in it.

    The label is required, and that is the whole difference between this fallback and
    scraping every unparseable body for ``error=``. What the cascade needs from this
    function is evidence about *who spoke*: an HTML block page carrying the text
    `error=invalid_grant` is a body the authorization server did not send, and reading a
    code out of it manufactures exactly the evidence the terminal branch of
    `_pre_flight_refresh` reasons about not having. A form-encoded body that arrives
    unlabelled therefore stays unclassified — which is the behaviour that was already
    there, so the gate adds no false-positive surface of its own.

    The status cannot stand in for any of this. §5.2 requires the body on every 400
    and 401, so a 400 without one did not come from the authorization server, and a
    non-compliant server that reports a revoked grant with 403 still names it here.
    """
    try:
        parsed = resp.json()
    except Exception:  # noqa: BLE001 — an unparseable body may still be the form-encoded shape
        pass
    else:
        # A body that parsed as JSON is the server's statement, `error` member or not — and
        # that includes JSON which is not an object at all. Never re-read it as a form: the
        # two spellings cannot both be authoritative, and only one of them was sent.
        code = parsed.get("error") if isinstance(parsed, dict) else None
        return code if isinstance(code, str) else None

    media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        return None
    try:
        codes = [v for k, v in parse_qsl(resp.text) if k == "error"]
    except Exception:  # noqa: BLE001 — a body that will not parse as a form is not one of these
        return None
    # Exactly one, or nothing. A compliant server sends `error` once; two of them mean a
    # mangled or concatenated body, and picking a winner would be a guess about which
    # half is the server's. `None` routes it to the terminal branch, which is the side of
    # the asymmetry that costs a message rather than a browser prompt. An empty value is
    # dropped by `parse_qsl` for the same reason: it matches neither code set anyway, and
    # "no OAuth error body" is the more honest of the two messages it could produce.
    if len(codes) != 1:
        return None
    # §5.2 defines the value over NQSCHAR, which excludes whitespace — so stripping can
    # only recover a code a line-oriented intermediary padded, never manufacture one that
    # was not sent. Without it a trailing newline is enough to turn `invalid_grant` into
    # an unrecognised code, which is the request-faulted message rather than the browser
    # prompt: the exact failure this fallback exists to close, back on one added byte.
    # `or None` keeps an all-whitespace value on the "no OAuth error body" path instead
    # of handing `""` to the branch that reports a rejected request.
    return codes[0].strip() or None


async def _pre_flight_refresh(server_name: str, storage: FileTokenStorage) -> None:
    """Refresh access token if near/past expiry via plain httpx (no MCP SDK).

    Renews the access token out-of-band before the session opens, so
    get_tokens() returns a live credential instead of None. Load-bearing: the
    official `mcp` SDK's silent refresh branch is unreachable at cold start, so
    without this the SDK sends the stale token blind → 401 → browser re-auth.
    Mirrors the mcpgen pre-flight. See the module docstring
    for the verified mechanism and version caveat.

    Raises
    ------
    ReauthenticationRequired
        The credential is gone: nothing cached to refresh with (no refresh_token, no
        client_id, no token_endpoint), or the authorization server named it dead
        (`invalid_grant`, `invalid_client`, `unauthorized_client`) on any status
        including 200. Browser login fixes it.
    TokenRefreshUnavailable
        Everything else. The authorization server was unreachable, something in front
        of it answered, it faulted the request rather than the credential, or a 200
        carried something that is not a token. The refresh token is untouched — a
        browser round has nothing to replace.
    """
    data = storage._load()
    entry = data.get(server_name, {})
    tokens_raw = entry.get("tokens") or {}

    expires_at = tokens_raw.get("expires_at")
    if expires_at is None or time.time() < expires_at - _MARGIN:
        return  # token fresh or no expiry info; nothing to do

    refresh_token = tokens_raw.get("refresh_token")
    if not refresh_token:
        raise ReauthenticationRequired(f"No refresh_token for '{server_name}'. Run: mcpgen login {server_name}")

    client_id = entry.get("client_info", {}).get("client_id")
    if not client_id:
        raise ReauthenticationRequired(f"No client_id cached for '{server_name}'. Run: mcpgen login {server_name}")

    token_endpoint = entry.get("token_endpoint")
    if not token_endpoint:
        raise ReauthenticationRequired(
            f"No token_endpoint cached for '{server_name}' (credentials pre-date this version). "
            f"Run: mcpgen login {server_name}"
        )

    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    client_secret = entry.get("client_info", {}).get("client_secret")
    if client_secret:
        payload["client_secret"] = client_secret

    # Classify what comes back. Treating every failure as "log in again" sends the
    # user to the browser for an outage the browser cannot fix — and, in a batch,
    # once per item. The grant and the server that renews it fail independently.
    #
    # `Accept: application/json` asks for the shape the classification below reads most
    # directly — the RFC 6749 §5.2 error body that tells the authorization server apart from
    # whatever stands in front of it. It is a request, not a guarantee: a server that answers
    # form-encoded by default (GitHub's token endpoint does) is free to ignore it. What makes
    # a rejection from such a server classifiable is the form-encoded fallback in
    # `_oauth_error_code`; the header only spares most servers the trip through it.
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_endpoint, data=payload, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001 — the Raises contract above admits exactly two types
        # Not `httpx.HTTPError`: `InvalidURL` and `CookieConflict` derive from `Exception`
        # directly, and a `token_endpoint` read from a hand-editable credentials file can
        # produce the first. Letting those escape unclassified is the exact failure this
        # function exists to remove, so the catch is as wide as the promise.
        raise TokenRefreshUnavailable(
            f"Could not reach the token endpoint for '{server_name}': {_describe(exc)}. "
            f"The refresh token is untouched; retry when the authorization server is back."
        ) from exc

    # A 200 is decided by whether it *is* a token, and the error code is not even read
    # unless that fails. Some servers pad a good token response with a blank `error`
    # member; consulting the code first would fail a refresh that plainly succeeded.
    if resp.status_code == 200:
        try:
            token = OAuthToken(**resp.json())
        except Exception as exc:  # noqa: BLE001 — JSON, pydantic, and TypeError all mean the same here
            # A 200 that is not a token is either an interstitial served with the
            # wrong status or a server reporting failure in-band — Slack's rotation
            # endpoint answers `{"ok": false, "error": ...}` this way. Both can be a
            # dead grant, and neither is worth guessing about, so name the command
            # that settles it rather than leaving no way forward.
            #
            # Both raises below are `from None`, and that is a redaction measure, not a
            # style choice. `exc` is the pydantic `ValidationError` from `OAuthToken(**…)`,
            # which quotes the rejected `input_value` back — on this path, the token
            # response. Redacting the *message* while chaining the exception that carries
            # the body only moves the leak: the chain travels with the object to every
            # caller, and the CLI commands that are not `login` do not catch these types,
            # so the interpreter prints the whole chain to stderr — into CI logs, which is
            # the audience this redaction exists for. `from None` suppresses the context on
            # every consumer at once, including library callers and the generated runner,
            # rather than relying on each one to catch.
            #
            # pydantic truncates that `input_value` repr, so what escapes is a prefix of
            # the credential rather than all of it — which is a smaller leak, not a
            # different kind, and a short secret still escapes whole.
            #
            # What the chain carried beyond the secret is the per-field error detail, and
            # the redacted body below already shows the offending non-secret members —
            # `_redact_secrets` drops only the credential-bearing ones. If the pydantic
            # error codes are ever wanted, they belong in this message via
            # `exc.errors(include_input=False)`, not back in the chain.
            if _oauth_error_code(resp) in _DEAD_GRANT_ERRORS:
                raise ReauthenticationRequired(
                    f"The token endpoint for '{server_name}' reported a dead credential with a 200: "
                    f"{_body_excerpt(resp)}. Run: mcpgen login {server_name}"
                ) from None
            # `type(exc).__name__`, not `_describe(exc)`: the same `input_value` reason as
            # above, one layer in — the message would otherwise quote what the chain no
            # longer does.
            raise TokenRefreshUnavailable(
                f"The token endpoint for '{server_name}' returned 200 but not a token "
                f"({type(exc).__name__}). Body: {_body_excerpt(resp)}. The refresh token is "
                f"untouched; retry later, and if it persists run: mcpgen login {server_name}"
            ) from None
        await storage.set_tokens(token)
        return

    # Past here the response is a failure, and what it *says* decides which kind. The
    # credential is dead only when the authorization server names which of its own
    # credentials died, in its own error format. Status is not a substitute: a 400
    # from a WAF and a 400 from the server carrying `invalid_grant` are different
    # events, and only the second one is fixed by opening a browser.
    error_code = _oauth_error_code(resp)

    # Ordering below is deliberate: the dead-grant codes are checked before the
    # retryable statuses, so a 503 carrying `invalid_grant` is read as the grant.
    # A proxy does not invent that code, so the server did speak; the cost of being
    # wrong is one browser prompt, while the reverse error — filing a genuine
    # revocation as retryable — never prompts at all.
    if error_code in _DEAD_GRANT_ERRORS:
        raise ReauthenticationRequired(
            f"Token refresh failed ({resp.status_code}, {error_code}): "
            f"{_body_excerpt(resp)}. Run: mcpgen login {server_name}"
        )

    # 5xx, the retryable statuses, and the codes that name a passing condition can
    # only be the authorization server or something in front of it, never the grant.
    if (
        resp.status_code >= 500
        or resp.status_code in _RETRYABLE_REFRESH_STATUS
        or error_code in _RETRYABLE_REFRESH_ERRORS
    ):
        # "Retry later" is not actionable on its own. A 429 or 503 that says when is
        # the difference between a scripted backoff and a guess, so pass it through.
        retry_after = resp.headers.get("retry-after")
        when = f" Retry-After: {retry_after}." if retry_after else ""
        raise TokenRefreshUnavailable(
            f"Token refresh failed ({resp.status_code}) for '{server_name}': "
            f"{_body_excerpt(resp)}. The refresh token is untouched; retry later.{when}"
        )

    # An error code in neither set: the server faulting the *request* rather than the
    # credential — invalid_request, unsupported_grant_type, invalid_scope — where
    # logging in again sends the identical refresh request back. A code from outside
    # the RFC lands here too and could be either, which is why this still names the
    # command. Lead with the diagnosis, since that is what it usually is; keep the way
    # out, since a code nobody here has seen might not be.
    if error_code is not None:
        raise TokenRefreshUnavailable(
            f"The authorization server for '{server_name}' rejected the refresh request itself "
            f"({resp.status_code}, {error_code}): {_body_excerpt(resp)}. The refresh token "
            f"is untouched, so this is most likely a client or server configuration problem rather "
            f"than a dead credential. If the configuration checks out, run: mcpgen login {server_name}"
        )

    # No error code at all — a 403 from a WAF, a 3xx to a captive portal, a 404 from
    # a moved endpoint, a bare 401 from an auth proxy, or a 2xx that is not 200. §5.2
    # requires the body above on a real rejection, so nothing here reached the
    # authorization server as a token request. Name the manual fallback: if it is a
    # server too terse to comply, this message is the only thing pointing at the
    # command that recovers.
    #
    # This resolves the same "headless callers get no browser prompt" question as
    # `_DEAD_GRANT_ERRORS` above, and resolves it the other way. There, a code the server
    # itself emitted names the credential, so the prompt is warranted and withholding it
    # would strand automation on a repairable condition. Here there is no such statement —
    # a bare status is far more often a proxy than a spec-violating authorization server —
    # so prompting every batch item on every block page costs more than it saves. The
    # printed command is a worse remedy than an automatic prompt, and is the right one only
    # because the evidence for a dead credential is absent rather than present.
    raise TokenRefreshUnavailable(
        f"Token refresh for '{server_name}' was answered with {resp.status_code} and no OAuth error "
        f"body, which is not how an authorization server reports a bad grant: "
        f"{_body_excerpt(resp)}. The refresh token is untouched; retry later, and if it "
        f"persists run: mcpgen login {server_name}"
    )


@asynccontextmanager
async def _open_http(url: str, *, headers: dict[str, str] | None = None, auth: httpx.Auth | None = None):
    """Open a StreamableHTTP transport, yielding (read, write, get_session_id).

    Wraps ``streamable_http_client`` (the non-deprecated successor to the
    removed ``streamablehttp_client``) with an MCP-default httpx client so
    callers can still inject headers or an auth handler.
    """
    async with create_mcp_http_client(headers=headers, auth=auth) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            yield streams


@asynccontextmanager
async def _http_session(
    server_name: str,
    server_url: str,
    *,
    client_name: str | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
):
    """OAuth-authenticated HTTP MCP session. Pre-flight refresh before connecting.

    creds_path: read tokens from this file instead of DEFAULT_CREDS_PATH. Must match
        whatever login() wrote to, or the session reads an empty store.
    """
    storage = FileTokenStorage(
        server_name, creds_path or DEFAULT_CREDS_PATH, backend=resolve_cred_backend(cred_backend)
    )
    await _pre_flight_refresh(server_name, storage)

    # Unlike login(), this does NOT clear a stale confidential-client registration,
    # and must not: a pre-fix credential still refreshes fine here, because
    # _pre_flight_refresh sends client_id + client_secret in the body — that is
    # client_secret_post, a *single* auth method, so it never hit the double-auth
    # bug. Once the refresh token dies, _no_browser raises ReauthenticationRequired
    # → "Run: mcpgen login" → which does clear and re-register as a public client.
    data = storage._load()
    redirect_uris = data.get(server_name, {}).get("client_info", {}).get("redirect_uris", [])
    callback_uri = redirect_uris[0] if redirect_uris else "http://localhost:0/callback"

    async def _no_browser(url: str) -> None:
        raise ReauthenticationRequired(f"OAuth re-auth needed for '{server_name}'. Run: mcpgen login {server_name}")

    async def _no_callback() -> tuple[str, str | None]:
        raise ReauthenticationRequired(f"OAuth re-auth needed for '{server_name}'. Run: mcpgen login {server_name}")

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=_client_metadata(server_name, callback_uri, client_name),
        storage=storage,
        redirect_handler=_no_browser,
        callback_handler=_no_callback,
    )

    async with _open_http(server_url, auth=provider) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


@asynccontextmanager
async def _stdio_session(command: str, args: list[str], env: dict[str, str] | None = None):
    """Stdio MCP session — no auth.

    env keys are merged on top of the SDK's safe inherited environment:
    ``{**get_default_environment(), **env}``.  The SDK's default allowlist is
    ``HOME, LOGNAME, PATH, SHELL, TERM, USER``; any keys not in that list (e.g.
    ``CONTEXT7_API_KEY``) must be passed explicitly via ``env`` — they are NOT
    automatically inherited from ``os.environ``.

    When ``env`` is None the child receives only ``get_default_environment()``.
    To forward shell env vars, use the ``--env KEY[=VAL]`` CLI flag, which
    constructs the ``env`` dict before calling this function.
    """
    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


@asynccontextmanager
async def _static_headers_session(url: str, headers: dict[str, str]):
    """HTTP MCP session with arbitrary static headers (e.g. from a config ``headers`` block).

    Bypasses OAuth entirely.  Supports any header, not just Authorization.
    """
    async with _open_http(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


@asynccontextmanager
async def _bearer_session(url: str, bearer: str):
    """HTTP MCP session authenticated with a static Bearer token (e.g. a GitHub PAT).

    Bypasses OAuth entirely — the caller is responsible for providing a valid token.
    The token is held only in memory and never written to disk.
    """
    async with _static_headers_session(url, {"Authorization": f"Bearer {bearer}"}) as s:
        yield s


@asynccontextmanager
async def session(
    server: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
    env: dict[str, str] | None = None,
):
    """Yield an initialized MCP ClientSession.

    cmd: if provided, use stdio transport (no auth).
    bearer: static Bearer token — routes through HTTP with Authorization header,
        bypassing OAuth. Intended for APIs that use PATs (e.g. GitHub). Takes
        precedence over OAuth when both url and bearer are provided.
    url: inline server URL — routes through HTTP + OAuth keyed by `server` name,
        overriding config. client_name: inline OAuth client_name override.
    config_path: read the server registry from this file instead of the default search.
    creds_path: read OAuth tokens from this file instead of DEFAULT_CREDS_PATH. Only
        meaningful on the OAuth path — the stdio, bearer, static-header, and raw-URL
        transports store no credentials.
    server: a configured name (servers()) → HTTP + OAuth; otherwise a raw URL.
    env: extra env vars forwarded to the stdio subprocess (merged over the SDK's
        safe allowlist). Keys NOT in ``get_default_environment()`` (e.g. API keys)
        must be supplied here; they are NOT inherited from ``os.environ`` otherwise.
        No-op for non-stdio transports.
    """
    _servers = servers(config_path=config_path)
    # SSE is discovered by discovery.py but has no transport adapter here. Refuse
    # up front rather than letting the URL fall into the Streamable HTTP path and
    # fail with an opaque protocol error.
    #
    # Only --stdio and --url are exempt, because only those re-target the
    # transport. --bearer must NOT exempt: it is an auth override, so with a
    # config-declared SSE entry the URL still resolves from that same entry and
    # `--bearer` would route an SSE endpoint into _bearer_session → Streamable
    # HTTP → exactly the opaque error this guard exists to prevent.
    #
    # An inline --url carries no declared type, so config-declared entries are
    # all this can catch; that limit is documented rather than guessed at from
    # URL shape.
    if cmd is None and url is None and _types_cache.get(server) == "sse":
        raise ValueError(
            f"server {server!r} uses SSE transport, which this mcpgen version does not support. "
            'Re-register it with "type": "http" if the server speaks Streamable HTTP, '
            "or pass --stdio to run it locally."
        )
    resolved_url = url or _servers.get(server)
    # Resolve a stdio spec: explicit --stdio flag takes precedence over config.
    stdio_spec: dict | None = None
    if cmd is not None:
        parts = shlex.split(cmd)
        stdio_spec = {"command": parts[0], "args": parts[1:], "env": env}
    elif server in _stdio_cache and url is None and bearer is None:
        stdio_spec = dict(_stdio_cache[server])
        if env:
            stdio_spec["env"] = {**(stdio_spec.get("env") or {}), **env}
    if stdio_spec is not None:
        async with _stdio_session(**stdio_spec) as s:
            yield s
    elif bearer is not None:
        target = resolved_url or server
        async with _bearer_session(target, bearer) as s:
            yield s
    elif resolved_url is not None and _headers_cache.get(server):
        # Config-supplied static headers (e.g. Authorization: Bearer ${PAT}) — bypass OAuth.
        async with _static_headers_session(resolved_url, _headers_cache[server]) as s:
            yield s
    elif resolved_url is not None:
        async with _http_session(
            server,
            resolved_url,
            client_name=client_name,
            cred_backend=cred_backend,
            creds_path=creds_path,
        ) as s:
            yield s
    elif "://" not in server:
        raise ValueError(f"server {server!r} not found in config and is not a URL")
    else:
        # Raw URL, no auth
        async with _open_http(server) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s


class _SessionBlock:
    """Owns every live session for one ``McpBridgeCaller.connected()`` block.

    All context managers are entered and exited by a single owner task. This is
    load-bearing, not stylistic: ``stdio_client`` and ``streamable_http_client``
    open anyio task groups, and anyio requires a cancel scope to be exited in the
    task that entered it. A session opened lazily inside an ``asyncio.gather()``
    child and closed from the parent would raise "Attempted to exit cancel scope
    in a different task". Funnelling every open through the owner removes that
    failure mode by construction.
    """

    def __init__(self, caller: McpBridgeCaller) -> None:
        self._caller = caller
        self._requests: asyncio.Queue = asyncio.Queue()
        self._sessions: dict[str, Any] = {}
        # Guards the open path only. call_tool() itself runs unserialized, so
        # asyncio.gather() over wrappers stays genuinely concurrent — the SDK's
        # ClientSession multiplexes on JSON-RPC request id.
        self._open_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _owner(self) -> None:
        """Serve open-requests until a None sentinel arrives, then unwind."""
        async with AsyncExitStack() as stack:
            # Entering an empty AsyncExitStack cannot fail, so signalling ready
            # here cannot deadlock the starter.
            self._ready.set()
            while True:
                item = await self._requests.get()
                if item is None:
                    return
                server, future = item
                try:
                    opened = await stack.enter_async_context(self._caller._session_cm(server))
                except BaseException as exc:  # noqa: BLE001 — relayed to the requester
                    if not future.done():
                        future.set_exception(exc)
                else:
                    if future.done():
                        # Requester was cancelled while waiting. The session is
                        # already on the stack and will close at block exit.
                        continue
                    future.set_result(opened)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._owner())
        await self._ready.wait()

    async def close(self) -> None:
        """Stop the owner and wait for the stack to unwind in the owner's task."""
        await self._requests.put(None)
        if self._task is not None:
            await self._task

    async def session_for(self, server: str) -> Any:
        """Return this block's session for *server*, opening it on first use."""
        existing = self._sessions.get(server)
        if existing is not None:
            return existing
        async with self._open_lock:
            existing = self._sessions.get(server)
            if existing is not None:
                return existing
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            await self._requests.put((server, future))
            opened = await future
            self._sessions[server] = opened
            return opened


class McpBridgeCaller:
    """McpCaller implementation backed by the standalone MCP client."""

    def __init__(
        self,
        *,
        cmd: str | None = None,
        url: str | None = None,
        bearer: str | None = None,
        client_name: str | None = None,
        config_path: str | Path | None = None,
        cred_backend: str | None = None,
        creds_path: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._cmd = cmd
        self._url = url
        self._bearer = bearer
        self._client_name = client_name
        self._config_path = config_path
        self._cred_backend = cred_backend
        self._creds_path = creds_path
        self._env = env
        self._block: _SessionBlock | None = None

    def _session_cm(self, server: str):
        """The session context manager for *server* under this caller's config."""
        return session(
            server,
            cmd=self._cmd,
            url=self._url,
            bearer=self._bearer,
            client_name=self._client_name,
            config_path=self._config_path,
            cred_backend=self._cred_backend,
            creds_path=self._creds_path,
            env=self._env,
        )

    @asynccontextmanager
    async def connected(self):
        """Reuse one initialized session per server for the duration of the block.

        Inside the block every ``call()`` to the same server reuses one
        ``ClientSession``: one ``initialize()``, one stdio subprocess, one OAuth
        pre-flight refresh. Outside the block ``call()`` is unchanged — it opens
        and closes a session per invocation.

            caller = McpBridgeCaller(cmd="python server.py")
            async with caller.connected():
                await gh.get_me(caller)
                await gh.list_issues(caller, owner="octocat", repo="hello-world")

        Connection arguments are fixed per caller instance, so within one block
        the server name fully determines the connection; two callers never share
        a session. Not re-entrant. Sessions close on exit, including when the
        block body raises.
        """
        if self._block is not None:
            raise RuntimeError("McpBridgeCaller.connected() is not re-entrant")
        block = _SessionBlock(self)
        await block.start()
        self._block = block
        try:
            yield self
        finally:
            self._block = None
            await block.close()

    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        block = self._block
        if block is None:
            # One-shot: open, call, close. Unchanged behaviour.
            async with self._session_cm(server) as s:
                return await self._invoke(s, tool, arguments)
        return await self._invoke(await block.session_for(server), tool, arguments)

    @staticmethod
    async def _invoke(s: Any, tool: str, arguments: dict) -> Any:
        result = await s.call_tool(tool, arguments)
        content = [_summarize_content_item(item) for item in result.content]
        return parse(content)


def _summarize_content_item(item: Any) -> dict:
    """Reduce one MCP content block to a compact, JSON-safe dict.

    ``text`` blocks keep today's shape ({"type", "text"}). ``image`` / ``resource`` /
    ``resource_link`` blocks carry no useful `.text` — capturing only `.text` (as the
    old code did) silently degrades them to an empty string, hiding the payload from
    shape inference entirely. Record presence/metadata instead (never the raw
    base64/blob bytes, to keep shape-specs small).
    """
    item_type = getattr(item, "type", None)
    if item_type == "image":
        return {
            "type": "image",
            "mimeType": getattr(item, "mimeType", None),
            "has_data": bool(getattr(item, "data", None)),
        }
    if item_type == "resource":
        resource = getattr(item, "resource", None)
        return {
            "type": "resource",
            "mimeType": getattr(resource, "mimeType", None),
            "has_text": bool(getattr(resource, "text", None)),
            "has_blob": bool(getattr(resource, "blob", None)),
        }
    if item_type == "resource_link":
        uri = getattr(item, "uri", None)
        return {
            "type": "resource_link",
            "uri": str(uri) if uri is not None else None,
            "name": getattr(item, "name", None),
        }
    return {"type": item_type, "text": getattr(item, "text", "")}


def _parse_one(item: Any) -> Any:
    """Parse a single MCP content block into a Python value.

    Falls back to ``ast.literal_eval`` for Python-repr payloads (e.g. servers
    that return single-quoted dicts like sqlite), then to a plain string as a
    last resort so callers can still inspect the response.

    Non-text content blocks (image / resource / resource_link) carry no `.text`
    to parse — return the block's own summary dict as-is rather than falling
    through to an empty string, so the shape-spec records something real.
    """
    item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
    if item_type in ("image", "resource", "resource_link"):
        return item if isinstance(item, dict) else _summarize_content_item(item)
    text = item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text


def parse(content_items: list) -> Any:
    """Extract the payload from an MCP tool result.

    MCP serializes a list return value as one content block per element, so a
    multi-block result must fold into a list — reading only the first block
    silently drops data, which is what this function used to do.

    A single block returns its value directly rather than a one-element list:
    that is the overwhelmingly common case and wrapping it would change the
    return type of every existing wrapper.

    ``structuredContent`` is deliberately not consulted. Its presence depends on
    whether the tool author annotated the return type, and its wrapping depends
    on whether the value is a JSON object, so relying on it would make
    correctness a function of someone else's type hints. Content blocks are
    transport-level and always present.
    """
    if not content_items:
        raise ValueError("MCP tool result has empty content")
    if len(content_items) == 1:
        return _parse_one(content_items[0])
    return [_parse_one(item) for item in content_items]


# ---------------------------------------------------------------------------
# Login flow (browser-based initial OAuth)
# ---------------------------------------------------------------------------


def _parse_callback_query(query: str) -> tuple[str | None, str | None]:
    """Extract (code, state) from an OAuth redirect query string.

    Shared by both callback paths — the local HTTP server and the headless
    paste-the-URL prompt — so an authorization denial surfaces identically in
    either mode.

    Raises ValueError when the authorization server returned an error.
    """
    params = parse_qs(query)
    error = params.get("error", [None])[0]
    if error:
        description = params.get("error_description", ["(no description)"])[0]
        raise ValueError(f"OAuth authorization failed: {error} — {description}")
    return params.get("code", [None])[0], params.get("state", [None])[0]


def _is_headless() -> bool:
    """Return True when no interactive browser is reachable (container, no display).

    MCPGEN_HEADLESS overrides the detection in both directions: set it to
    1/true/yes/on to force the paste-the-URL flow, or to anything else (e.g. 0)
    to force the browser flow.
    """
    override = os.environ.get("MCPGEN_HEADLESS")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    # macOS/Windows: the user's own desktop — a browser is always reachable.
    if sys.platform in ("darwin", "win32"):
        return False
    return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


async def _local_callback_server(port: int = 0) -> tuple[int, asyncio.Future]:
    """Start a local HTTP server to receive the OAuth redirect. Returns (port, future)."""
    loop = asyncio.get_event_loop()
    future: asyncio.Future[tuple[str | None, str | None]] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        code: str | None = None
        state: str | None = None
        failure: ValueError | None = None
        try:
            data = await reader.read(4096)
            first_line = data.decode(errors="replace").split("\n")[0]
            path = first_line.split(" ")[1] if " " in first_line else ""
            try:
                code, state = _parse_callback_query(urlparse(path).query)
            except ValueError as exc:
                failure = exc
            body = (
                b"<html><body><h1>Login failed. You can close this tab.</h1></body></html>"
                if failure is not None
                else b"<html><body><h1>Login complete. You can close this tab.</h1></body></html>"
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            if not future.done():
                if failure is not None:
                    future.set_exception(failure)
                else:
                    future.set_result((code, state))

    try:
        server = await asyncio.start_server(_handle, "localhost", port)
    except OSError as exc:
        if exc.errno == _errno.EADDRINUSE and port != 0:
            server = await asyncio.start_server(_handle, "localhost", 0)
        else:
            raise

    actual_port = server.sockets[0].getsockname()[1]

    async def _serve_until_done() -> None:
        async with server:
            # The future may carry an OAuth error (see _handle). Swallow it here —
            # this task exists only to keep the socket open until the callback
            # lands; the exception is delivered to callback_handler, which awaits
            # the same future.
            with suppress(BaseException):
                await future

    asyncio.create_task(_serve_until_done())
    return actual_port, future


def _persist_token_endpoint(
    storage: FileTokenStorage,
    server_name: str,
    provider: OAuthClientProvider | None,
) -> None:
    """Cache the exact token endpoint the SDK exchanged this token against.

    `_pre_flight_refresh` refuses to renew without it, so this has to be saved
    wherever a token is — including the paths where the session itself failed.
    Runs twice when the session fails *after* `initialize()`; the second write is
    the same value, so it costs a load/save and nothing else.

    `_get_token_endpoint()` is the SDK's own resolution: the discovered
    `oauth_metadata.token_endpoint`, or `<origin>/token` when the server publishes
    no discovery document. Asking it — rather than reading the metadata ourselves
    and improvising a fallback — is what makes the cached URL the one that just
    demonstrably worked: we are only ever called with a token in hand, so whatever
    endpoint issued it is proven, while any endpoint we reconstruct is a guess.

    It is private API, and access is direct on purpose: a `getattr` chain would
    turn an SDK rename into a silent no-op — no endpoint written, every credential
    expiring into a browser prompt — which is the failure this path exists to stop.
    `mcp` is pinned to a range, so let a rename raise loudly.
    `test_sdk_provider_resolves_token_endpoint` pins the name cheaply.
    """
    if provider is None:  # only reachable before the handshake, when no token exists
        return
    endpoint = provider._get_token_endpoint()
    if not endpoint:
        return
    data = storage._load()
    data.setdefault(server_name, {})["token_endpoint"] = str(endpoint)
    storage._save(data)


_DESCRIBE_LIMIT = 200
"""Per-leaf cap on the exception text in a one-line error. Shared with the response-body
bound in ``_pre_flight_refresh``: enough to identify the failure, short enough to read."""

_DESCRIBE_TOTAL_LIMIT = 600
"""Cap on a whole flattened exception group. The per-leaf bound alone lets an N-leaf
group produce an N × ~215-character "one-line" message; this is what keeps it one line."""


def _carries_interrupt(exc: BaseException) -> bool:
    """True if *exc* is, or wraps, a control-flow exception we must not relabel.

    anyio task groups wrap even a single exception, so a Ctrl-C raised inside the
    session surfaces as ``BaseExceptionGroup([KeyboardInterrupt])`` — a flat
    isinstance check on the outermost exception would miss it and report a server
    fault instead of an interrupt.

    A group holding *both* an interrupt and a real failure counts as an interrupt
    and propagates unconverted. That is deliberate: the user asked to stop, and
    the group still carries the other exception for anyone who wants it, whereas
    converting would discard the interrupt and keep the process running.
    """
    if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_carries_interrupt(inner) for inner in exc.exceptions)
    return False


def _describe(exc: BaseException) -> str:
    """Render *exc* with its real cause visible, flattening exception groups.

    ``str(BaseExceptionGroup)`` is only ever "unhandled errors in a TaskGroup
    (1 sub-exception)" — useless to whoever has to decide whether the server
    returned 502, DNS failed, or TLS did. The transport error always arrives
    wrapped, so the leaves are the whole message.

    Each leaf is capped: an ``HTTPStatusError`` can carry a whole HTML error page
    in its message, and this ends up on one CLI line. Same reasoning — and same
    bound — as the response-body truncation in ``_pre_flight_refresh``. The joined
    result is capped too: leaves are bounded but their *number* is not, so the
    per-leaf bound alone does not keep a wide group to one readable line.

    Each leaf is also redacted, for a reason that is not hypothetical: the MCP SDK
    reports a token or registration response that fails validation by interpolating the
    pydantic error, which quotes the rejected body back as a Python repr. `login()` puts
    the result of this function into `PostLoginCheckFailed`, and `cli.py` prints that to
    stderr — so without the pass a gateway that camelCases its members turned a failed
    post-login check into a credential in the CI log. Redaction runs per leaf and before
    both caps, so a secret cannot survive by sitting past one.
    """
    if isinstance(exc, BaseExceptionGroup):
        joined = "; ".join(_describe(inner) for inner in exc.exceptions)
        if len(joined) > _DESCRIBE_TOTAL_LIMIT:
            joined = joined[:_DESCRIBE_TOTAL_LIMIT] + "…"
        return joined
    text = _redact_secret_text(str(exc))
    if len(text) > _DESCRIBE_LIMIT:
        text = text[:_DESCRIBE_LIMIT] + "…"
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


async def login(
    server_name: str,
    creds_path: Path = DEFAULT_CREDS_PATH,
    *,
    url: str | None = None,
    client_name: str | None = None,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
    headless: bool | None = None,
    callback_timeout: float | None = None,
) -> None:
    """Full browser-based OAuth login for server_name. Caches tokens + token_endpoint.

    url/client_name: inline overrides (no config entry needed).
    config_path: read the server registry from this file instead of the default search.
    headless: True prints the authorization URL and reads the pasted callback URL
        from stdin instead of opening a browser; False forces the browser flow;
        None (default) auto-detects via _is_headless().
    callback_timeout: seconds to wait for the browser redirect; None (default) uses
        _CALLBACK_TIMEOUT, and any value <= 0 waits indefinitely. Ignored in
        headless mode, where the stdin read is never bounded.

    Raises PostLoginCheckFailed when the OAuth flow succeeded but the post-login
    session failed: the token is saved and re-running login will not help.

    Raises LoginWontHelp when the credential store cannot be parsed *and* cannot be
    moved aside. The fix is a human moving that file, so no browser round is attempted.
    """
    _servers = servers(config_path=config_path)
    server_url = url or _servers.get(server_name)
    if server_url is None:
        raise ValueError(f"Unknown server {server_name!r}. Pass --url or add it to config. Known: {list(_servers)}")

    storage = FileTokenStorage(server_name, creds_path, backend=resolve_cred_backend(cred_backend))

    # Stash the existing entry before clearing it. If the OAuth flow fails before
    # it produces a token (user cancel, network error, bad registration), we
    # restore it so the caller is not locked out of a previously-working server.
    # Once a token *has* been exchanged the stash is stale by definition — see
    # the handler at the bottom of this function.
    #
    # A corrupt store must not stop this read: `mcpgen login` is the command whose
    # whole job is producing a fresh valid entry, so failing it on unparseable JSON
    # leaves hand-deleting the file as the only route back. Falling straight through
    # to `{}` is not the answer either — the `_save` two lines down would then write
    # that empty view over every other server's entry, some of which may still be
    # recoverable by hand. So: move the bad file aside, keep it, and start clean.
    # Scoped to `login()` on purpose. `_file_load` itself keeps raising, because the
    # other readers (`_pre_flight_refresh`, the SDK's mid-flow reads) run with nobody
    # at the keyboard, and "start fresh" is a decision only this command's caller made.
    try:
        data = storage._load()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # Both are ValueError, and both mean the same thing: bytes on disk that are not a
        # store. UnicodeDecodeError comes from the `read_text` a line before `json.loads`,
        # so catching only the decode error would let a file saved in another encoding
        # through as a traceback. `_require_store` raises the first of the two for a file
        # that parses into something other than an object — `[]`, `null`, a bare string —
        # so that shape lands here rather than on `data.pop` two lines down.
        # Deliberately narrower than OSError — an unreadable *path*
        # (permissions, a directory in the way) is not a corrupt store and must propagate.
        # Nanoseconds, not seconds: two corrupt-store logins inside the same second would
        # otherwise `os.replace` the second quarantine over the first, destroying the bytes
        # this exists to keep.
        quarantine = storage._path.with_name(f"{storage._path.name}.corrupt.{time.time_ns()}")
        try:
            os.replace(storage._path, quarantine)
        except OSError as move_failed:
            # Continuing here would `_save({})` over the very bytes the quarantine exists
            # to preserve, after printing a promise to preserve them. Stop instead: the
            # file is still there, and the message says what to do with it.
            # `LoginWontHelp` and not one of its subclasses: both would assert something
            # untrue here. `PostLoginCheckFailed` claims a token is cached, and nothing was
            # even read; `TokenRefreshUnavailable` claims the token endpoint answered, and
            # it was never contacted. The base says the one thing that is true and the one
            # thing a caller can act on — another browser round changes nothing, because the
            # fix is a human moving a file. It also gets the message out of `cli.py` and the
            # generated runner as a message rather than a traceback: both already catch the
            # base, which is what that forward-looking clause was for.
            raise LoginWontHelp(
                f"{storage._path} is not a readable credential store ({exc}) and could not be moved aside "
                f"({move_failed}). Move or delete it by hand, then run login again."
            ) from exc
        print(
            f"[mcpgen] {storage._path} is not a readable credential store ({exc}); moved to {quarantine}. "
            f"Other servers' entries are in that file if you need them.",
            file=sys.stderr,
            flush=True,
        )
        data = {}
    stashed = data.pop(server_name, None)
    storage._save(data)

    if headless is None:
        headless = _is_headless()
    timeout = _CALLBACK_TIMEOUT if callback_timeout is None else callback_timeout

    provider: OAuthClientProvider | None = None

    try:
        callback_future: asyncio.Future | None = None

        if headless:
            # No local server: the redirect URI is never actually fetched, the
            # user pastes it back. Port-less keeps the registered URI stable
            # across runs, which matters for servers that pin redirect_uris.
            callback_uri = "http://localhost/callback"

            async def redirect_handler(url: str) -> None:
                print(f"\nOpen this URL in your browser:\n\n{url}\n", file=sys.stderr, flush=True)

            async def callback_handler() -> tuple[str, str | None]:
                print(
                    "After authorizing, paste the full callback URL here (http://localhost.../callback?code=...):",
                    file=sys.stderr,
                    flush=True,
                )
                # run_in_executor: a bare sys.stdin.readline() would block the loop.
                loop = asyncio.get_running_loop()
                line = (await loop.run_in_executor(None, sys.stdin.readline)).strip()
                if not line:
                    raise ValueError("No URL pasted — login aborted.")
                code, state = _parse_callback_query(urlparse(line).query)
                if code is None:
                    raise ValueError(f"Pasted URL has no ?code= parameter: {line[:120]}")
                return code, state

        else:
            port, callback_future = await _local_callback_server()
            callback_uri = f"http://localhost:{port}/callback"

            async def redirect_handler(url: str) -> None:
                print(f"\nOpening browser: {url}\n")
                webbrowser.open(url)

            async def callback_handler() -> tuple[str, str | None]:
                print("Waiting for OAuth callback… (complete login in your browser)")
                assert callback_future is not None
                if timeout <= 0:  # opt-out: wait indefinitely
                    return await callback_future
                try:
                    # wait_for cancels the future on timeout; _serve_until_done
                    # awaits the same future under suppress(), so the background
                    # task exits cleanly instead of dangling.
                    return await asyncio.wait_for(callback_future, timeout)
                except TimeoutError:
                    raise TimeoutError(
                        f"No OAuth callback received within {timeout}s. The browser never "
                        "returned to mcpgen — some authorization servers just close the tab when you "
                        "cancel, without redirecting back. Retry, or use --headless to paste the "
                        "redirect URL manually."
                    ) from None

        provider = OAuthClientProvider(
            server_url=server_url,
            client_metadata=_client_metadata(server_name, callback_uri, client_name),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

        try:
            try:
                async with _open_http(server_url, auth=provider) as (read, write, _):
                    async with ClientSession(read, write) as s:
                        await s.initialize()

                        # Persist the token endpoint for later pre-flight refresh. Do this
                        # independently of any tool call — not every server exposes `whoami`.
                        _persist_token_endpoint(storage, server_name, provider)

                        # Confirm the authenticated session works with a server-agnostic
                        # call. `list_tools` is part of the MCP protocol — every server
                        # supports it, unlike any specific tool name.
                        tools = await s.list_tools()
                        print(f"Login OK ({server_name}); {len(tools.tools)} tool(s) available")
            except OAuthRegistrationError as e:
                # `from None`: `_explain_registration_error` redacts the text it returns,
                # and chaining the original would put the unredacted `client_secret` back
                # in front of anyone printing a traceback. This type is outside the
                # `LoginWontHelp` taxonomy, so it escapes both `_cmd_login`'s catch and
                # `main()`'s roots — a traceback is its normal rendering, not its unlucky one.
                raise _explain_registration_error(e) from None
        finally:
            if callback_future is not None and not callback_future.done():
                callback_future.set_result((None, None))
            await asyncio.sleep(0)

    except BaseException as exc:
        # Did the flow get far enough to save a token? OAuthClientProvider writes
        # it from inside the auth handshake, before `initialize()` returns, so a
        # failure raised out of the session (a 502 from the origin, a transport
        # error on the first call) leaves a *usable* credential behind. Restoring
        # the stash over it would throw away the login the user just completed and
        # send the next run back to the browser — forever, since nothing ever
        # sticks. Re-read storage rather than trusting the provider object: the
        # SDK owns that write and this is the same seam it wrote through.
        # One read, reused for the restore below: nothing writes in between, and on
        # the keyring backend a second _load() is another keychain round-trip — one
        # that can even come from a different backend, if the first read tripped the
        # fallback to file.
        # The read itself can fail — a corrupt credentials.json, a keyring backend
        # that errors on read — and raising from inside this handler would replace
        # the transport error the operator actually needs to see.
        try:
            data = storage._load()
        except Exception:  # noqa: BLE001 — never mask the original failure
            # Unreadable store: nothing can be said about what the flow produced, and
            # restoring the stash on top of it is the one move that could make this
            # worse. Fall through to re-raising the original — unclassified, and
            # deliberately so. PostLoginCheckFailed would assert a token is cached,
            # which is exactly what could not be established. `mcpgen login` shows a
            # traceback for this; a store that cannot be read is not a routine
            # outcome, and the read error belongs on screen with it.
            data, restorable = {}, False
        else:
            restorable = True
        produced = data.get(server_name) or {}
        if not produced.get("tokens"):
            if stashed is not None and restorable:
                # The write fails for the same reasons the read does — a keyring backend
                # that has started refusing, a full disk, a permission change — and a
                # restore that could not happen must not become the exception the operator
                # sees. Best effort: the stash is a nicety, the original failure is the
                # answer. Same rule as the read above, and as the endpoint save below.
                with suppress(Exception):
                    data[server_name] = stashed
                    storage._save(data)
            raise

        # A token is only as good as the endpoint that can renew it: without one,
        # _pre_flight_refresh demands a new login the moment it expires — the very
        # re-prompt this branch exists to avoid. The normal persistence sits after
        # initialize(), which is what just failed, so do it here. A storage error
        # must not swallow the classification below, so it is carried into that
        # message rather than raised. It does not go through warnings.warn: this is
        # the one condition that silently reinstates the re-prompt this whole branch
        # exists to prevent, and a UserWarning is shown once per location and
        # disappears entirely under PYTHONWARNINGS=ignore. It belongs on the line the
        # operator is already reading.
        # What the message may claim depends on what is already on disk, not on which
        # branch got here: initialize() persists the endpoint too, so a list_tools()
        # failure can reach this point with one already cached. `produced` was read
        # after that write, so it answers the question directly.
        unrenewable = ""
        try:
            _persist_token_endpoint(storage, server_name, provider)
        except Exception as save_exc:  # noqa: BLE001 — never mask the original failure
            if produced.get("token_endpoint"):
                unrenewable = (
                    f" The token endpoint could not be updated ({save_exc}); the previously "
                    f"cached one is still on disk, so a refresh works only if it has not moved."
                )
            else:
                unrenewable = (
                    f" The token endpoint could not be cached ({save_exc}), so the saved token "
                    f"cannot be refreshed and the next run will prompt for a new login."
                )

        # Keep the token, and tell the caller the failure came after authentication —
        # but never relabel an interrupt as a failed check.
        if _carries_interrupt(exc):
            raise
        # State only what is known. A token was issued; that does not prove the
        # resource server accepted it — a post-login 401, an MCP-level error from
        # list_tools(), and a 502 from the origin all land here. What they share is
        # that another browser round cannot fix them, which is the one thing the
        # caller has to act on.
        # `from None`, for the same reason `_pre_flight_refresh`'s parse failures use it,
        # and reached through a different door: the SDK reports a token response that fails
        # validation as `OAuthTokenError(f"Invalid token response: {pydantic_error}")`, and
        # that text quotes the rejected body. `_describe` above redacts what the message
        # says; chaining the original would hand a library caller printing a traceback the
        # same credential the message no longer carries. `__context__` is still set, so the
        # original stays available to anything inspecting it programmatically — what goes is
        # only the default rendering, which is the entire leak surface.
        raise PostLoginCheckFailed(
            f"Login succeeded ({server_name}) and the token was saved, but the check that follows "
            f"it failed: {_describe(exc)}. Logging in again will not change this."
            f"{unrenewable}"
        ) from None

    print(f"Credentials saved to {creds_path}")


async def ensure_login(
    server_name: str,
    creds_path: Path = DEFAULT_CREDS_PATH,
    *,
    url: str | None = None,
    client_name: str | None = None,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
    headless: bool | None = None,
    callback_timeout: float | None = None,
) -> None:
    """Ensure a usable token exists for server_name, refreshing or logging in.

    Silent when a valid (or refreshable) token is cached — runs the same
    pre-flight refresh as a normal call. Opens the browser via login() only when
    there is no token, or the refresh token itself is gone/expired. Idempotent:
    safe to call before every run.

    Cases:
    - Fresh token: no-op.
    - Near/past expiry, refresh_token present: silent out-of-band renewal.
    - Near/past expiry with nothing cached to refresh with (no refresh_token, no
      client_id, no token_endpoint): browser login.
    - Near/past expiry and the authorization server answers `invalid_grant`,
      `invalid_client`, or `unauthorized_client`: browser login. The last two fault
      the registration rather than the token, and login() drops the cached
      `client_info`, so the SDK registers anew — it does replace what failed.
    - Near/past expiry and the renewal failed any other way — unreachable server, a
      block page, a request the server faulted: no browser login.
      `TokenRefreshUnavailable` propagates. The refresh token is intact in every one
      of those, so a browser round has nothing to replace and would repeat the same
      failure once per item across a batch.
    - No token at all: browser login.
    """
    storage = FileTokenStorage(server_name, creds_path, backend=resolve_cred_backend(cred_backend))
    try:
        await _pre_flight_refresh(server_name, storage)
    except ReauthenticationRequired:
        await login(
            server_name,
            creds_path,
            url=url,
            client_name=client_name,
            config_path=config_path,
            cred_backend=cred_backend,
            headless=headless,
            callback_timeout=callback_timeout,
        )
        return
    if await storage.get_tokens() is None:  # first-time: no token cached at all
        await login(
            server_name,
            creds_path,
            url=url,
            client_name=client_name,
            config_path=config_path,
            cred_backend=cred_backend,
            headless=headless,
            callback_timeout=callback_timeout,
        )


async def ensure_login_all(
    server_names: list[str],
    creds_path: Path = DEFAULT_CREDS_PATH,
    *,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
    headless: bool | None = None,
    callback_timeout: float | None = None,
) -> None:
    """Run ensure_login() for each server, one at a time.

    Sequential on purpose: a parallel version would open several browser tabs at
    once and race for stdin in headless mode. Servers with a valid cached token
    are silent, so the common case costs nothing.
    """
    for name in server_names:
        await ensure_login(
            name,
            creds_path,
            config_path=config_path,
            cred_backend=cred_backend,
            headless=headless,
            callback_timeout=callback_timeout,
        )
