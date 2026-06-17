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
import shlex
import stat
import time
import warnings
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

DEFAULT_CREDS_PATH = Path.home() / ".mcp-client-kit" / "credentials.json"
DEFAULT_CONFIG_PATH = Path.home() / ".mcp-client-kit" / "config.json"

# Credential backend — selects where OAuth tokens are stored.
# Resolution order (first wins):
#   1. CLI --cred-backend flag (passed through to FileTokenStorage)
#   2. MCP_KIT_CRED_BACKEND env var
#   3. ~/.mcp-client-kit/config.json  "cred_backend" key
#   4. default: "file"
_CRED_BACKEND_ENV = "MCP_KIT_CRED_BACKEND"
_VALID_BACKENDS: frozenset[str] = frozenset({"file", "keyring", "auto"})
_KEYRING_SERVICE: str = "mcp-client-kit"
_KEYRING_USER: str = "credentials"


def _load_client_config(path: Path | None = None) -> dict:
    """Load ~/.mcp-client-kit/config.json (or override path). Returns {} if absent/invalid."""
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
    tmp = target.with_suffix(".tmp")
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

    Resolution order: CLI arg → MCP_KIT_CRED_BACKEND env → config file → "file".
    Raises ValueError for unknown values at any level.
    """
    if cli_value is not None:
        if cli_value not in _VALID_BACKENDS:
            raise ValueError(
                f"Unknown cred backend {cli_value!r}. Valid choices: {sorted(_VALID_BACKENDS)}"
            )
        return cli_value
    env = os.environ.get(_CRED_BACKEND_ENV)
    if env:
        if env not in _VALID_BACKENDS:
            raise ValueError(
                f"{_CRED_BACKEND_ENV}={env!r} unknown. Valid choices: {sorted(_VALID_BACKENDS)}"
            )
        return env
    cfg = _load_client_config()
    backend = cfg.get("cred_backend")
    if backend:
        if backend not in _VALID_BACKENDS:
            raise ValueError(
                f"config cred_backend={backend!r} unknown. Valid choices: {sorted(_VALID_BACKENDS)}"
            )
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

def _keyring_read_raw() -> dict:
    """Read all credentials from the OS keyring. Raises on any failure."""
    import keyring as _kr  # lazy — tests can monkeypatch sys.modules["keyring"]
    raw = _kr.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    return json.loads(raw) if raw else {}


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
#   1. $MCP_KIT_SERVERS                       (explicit path)
#   2. ~/.mcp-client-kit/servers.json         ({"name": "url", ...} or mcpServers)
#   3. ./.mcp.json                            (Claude Code format: {"mcpServers": {...}})
# Any name not found here is treated as a raw URL (no auth). See servers.example.json.
_SERVERS_CONFIG_ENV = "MCP_KIT_SERVERS"
_SERVERS_SEARCH = [
    Path.home() / ".mcp-client-kit" / "servers.json",
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
                filtered_h = {
                    str(k): str(v)
                    for k, v in raw_headers.items()
                    if isinstance(v, (str, int, float)) and not isinstance(v, bool)
                }
                if filtered_h:
                    hdrs[name] = {k: os.path.expandvars(v) for k, v in filtered_h.items()}
        elif isinstance(val, dict) and val.get("command"):
            raw_args = val.get("args") or []
            args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
            raw_env = val.get("env") or {}
            env: dict[str, str] | None = None
            if isinstance(raw_env, dict):
                filtered = {
                    str(k): str(v)
                    for k, v in raw_env.items()
                    if isinstance(v, (str, int, float)) and not isinstance(v, bool)
                }
                if filtered:
                    env = {k: os.path.expandvars(v) for k, v in filtered.items()}
            cmds[name] = {"command": str(val["command"]), "args": args, "env": env}
    return urls, names, cmds, hdrs


def servers(*, refresh: bool = False, config_path: str | Path | None = None) -> dict[str, str]:
    """Return the {name: url} registry loaded from user config (cached).

    config_path: if given, read that file exclusively (authoritative — no env or
    search-order fallback) and always fresh, bypassing the cache.  A missing or
    unparseable explicit config raises rather than silently returning an empty dict.
    """
    global _servers_cache, _client_names_cache, _stdio_cache, _headers_cache
    if config_path is None and _servers_cache is not None and not refresh:
        return _servers_cache
    if config_path is not None:
        # Authoritative path — fail fast, no search-order fallback.
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"config not found: {config_path}")
        try:
            _servers_cache, _client_names_cache, _stdio_cache, _headers_cache = (
                _parse_servers(json.loads(path.read_text()))
            )
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
                _servers_cache, _client_names_cache, _stdio_cache, _headers_cache = (
                    _parse_servers(json.loads(path.read_text()))
                )
                return _servers_cache
            except (json.JSONDecodeError, OSError, AttributeError):
                continue
    _servers_cache, _client_names_cache, _stdio_cache, _headers_cache = {}, {}, {}, {}
    return _servers_cache


def _resolve_client_name(server_name: str) -> str:
    """OAuth client_name for a server: config override, else default template."""
    if _servers_cache is None:
        servers()
    return _client_names_cache.get(server_name) or f"mcp-client-kit ({server_name})"


# Treat a cached token as expired this many seconds before its real expiry.
_MARGIN = 120


class ReauthenticationRequired(Exception):
    """Tokens absent or refresh failed. Run: mcp-kit login <server>"""


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
                f"[mcp-client-kit] {self._path} had permissions {oct(mode)}; "
                "fixed to 0600.",
                stacklevel=3,
            )
        return json.loads(self._path.read_text())

    def _file_save(self, data: dict) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
        tmp = self._path.with_suffix(".tmp")
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
            f"[mcp-client-kit] keyring unavailable ({reason}); "
            f"falling back to hardened file at {self._path}.",
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
        ``~/.mcp-client-kit/credentials.json``).
    purge:
        When ``True``, remove only the migrated entries from the source backend
        after a verified write. When ``False`` (default), the source is kept.
    set_default:
        When ``True``, write ``cred_backend=<to_backend>`` into the client
        config file so future commands default to the new backend.
    config_path:
        Override path for the client config (default:
        ``~/.mcp-client-kit/config.json``).

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
            raise ValueError(
                f"{label}={val!r} is not a concrete backend. Valid choices: {sorted(concrete)}"
            )
    from_backend = _detect_keyring() if from_backend == "auto" else from_backend
    to_backend   = _detect_keyring() if to_backend   == "auto" else to_backend

    if from_backend == to_backend:
        raise ValueError(
            f"from_backend and to_backend are both {from_backend!r}; nothing to migrate."
        )

    # Read source
    source_all = _read_backend(from_backend, credentials_path)

    # Filter to requested servers
    if servers is not None:
        missing = [s for s in servers if s not in source_all]
        if missing:
            raise ValueError(
                f"Requested server(s) not found in {from_backend!r} backend: "
                + ", ".join(repr(s) for s in missing)
            )
        source_subset = {k: source_all[k] for k in servers}
    else:
        source_subset = source_all

    if not source_subset:
        return {"from": from_backend, "to": to_backend, "migrated": 0,
                "overwritten": 0, "purged": False, "set_default": False}

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
        Path to the file backend (default ``~/.mcp-client-kit/credentials.json``).
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
        out.append({
            "name": name,
            "expires_at": exp,
            "expired": expired,
            "has_refresh_token": bool(tok.get("refresh_token")),
        })
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
        Path to the file backend (default ``~/.mcp-client-kit/credentials.json``).
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


async def _pre_flight_refresh(server_name: str, storage: FileTokenStorage) -> None:
    """Refresh access token if near/past expiry via plain httpx (no MCP SDK).

    Renews the access token out-of-band before the session opens, so
    get_tokens() returns a live credential instead of None. Load-bearing: the
    official `mcp` SDK's silent refresh branch is unreachable at cold start, so
    without this the SDK sends the stale token blind → 401 → browser re-auth.
    Mirrors the mcp_client pre-flight. See the module docstring
    for the verified mechanism and version caveat.
    """
    data = storage._load()
    entry = data.get(server_name, {})
    tokens_raw = entry.get("tokens") or {}

    expires_at = tokens_raw.get("expires_at")
    if expires_at is None or time.time() < expires_at - _MARGIN:
        return  # token fresh or no expiry info; nothing to do

    refresh_token = tokens_raw.get("refresh_token")
    if not refresh_token:
        raise ReauthenticationRequired(
            f"No refresh_token for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    client_id = entry.get("client_info", {}).get("client_id")
    if not client_id:
        raise ReauthenticationRequired(
            f"No client_id cached for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    token_endpoint = entry.get("token_endpoint")
    if not token_endpoint:
        raise ReauthenticationRequired(
            f"No token_endpoint cached for '{server_name}' (credentials pre-date this version). "
            f"Run: mcp-kit login {server_name}"
        )

    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    client_secret = entry.get("client_info", {}).get("client_secret")
    if client_secret:
        payload["client_secret"] = client_secret

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_endpoint, data=payload)

    if resp.status_code != 200:
        raise ReauthenticationRequired(
            f"Token refresh failed ({resp.status_code}): {resp.text[:200]}. "
            f"Run: mcp-kit login {server_name}"
        )

    await storage.set_tokens(OAuthToken(**resp.json()))


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
):
    """OAuth-authenticated HTTP MCP session. Pre-flight refresh before connecting."""
    storage = FileTokenStorage(server_name, backend=resolve_cred_backend(cred_backend))
    await _pre_flight_refresh(server_name, storage)

    data = storage._load()
    redirect_uris = data.get(server_name, {}).get("client_info", {}).get("redirect_uris", [])
    callback_uri = redirect_uris[0] if redirect_uris else "http://localhost:0/callback"

    async def _no_browser(url: str) -> None:
        raise ReauthenticationRequired(
            f"OAuth re-auth needed for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    async def _no_callback() -> tuple[str, str | None]:
        raise ReauthenticationRequired(
            f"OAuth re-auth needed for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name=client_name or _resolve_client_name(server_name),
            redirect_uris=[callback_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
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
async def _bearer_session(url: str, bearer: str):
    """HTTP MCP session authenticated with a static Bearer token (e.g. a GitHub PAT).

    Bypasses OAuth entirely — the caller is responsible for providing a valid token.
    The token is held only in memory and never written to disk.
    """
    headers = {"Authorization": f"Bearer {bearer}"}
    async with _open_http(url, headers=headers) as (read, write, _):
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
async def session(
    server: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
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
    server: a configured name (servers()) → HTTP + OAuth; otherwise a raw URL.
    env: extra env vars forwarded to the stdio subprocess (merged over the SDK's
        safe allowlist). Keys NOT in ``get_default_environment()`` (e.g. API keys)
        must be supplied here; they are NOT inherited from ``os.environ`` otherwise.
        No-op for non-stdio transports.
    """
    _servers = servers(config_path=config_path)
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
        async with _http_session(server, resolved_url, client_name=client_name, cred_backend=cred_backend) as s:
            yield s
    elif not server.startswith(("http://", "https://")):
        raise ValueError(
            f"server {server!r} not found in config and is not a URL"
        )
    else:
        # Raw URL, no auth
        async with _open_http(server) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s


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
        env: dict[str, str] | None = None,
    ) -> None:
        self._cmd = cmd
        self._url = url
        self._bearer = bearer
        self._client_name = client_name
        self._config_path = config_path
        self._cred_backend = cred_backend
        self._env = env

    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        async with session(
            server,
            cmd=self._cmd,
            url=self._url,
            bearer=self._bearer,
            client_name=self._client_name,
            config_path=self._config_path,
            cred_backend=self._cred_backend,
            env=self._env,
        ) as s:
            result = await s.call_tool(tool, arguments)
            content = [
                {"type": item.type, "text": getattr(item, "text", "")}
                for item in result.content
            ]
            return parse(content)


def parse(content_items: list) -> Any:
    """Extract and JSON-parse the text payload from an MCP tool result.

    Falls back to ``ast.literal_eval`` for Python-repr payloads (e.g. servers
    that return single-quoted dicts like sqlite), then to a plain string as a
    last resort so callers can still inspect the response.
    """
    if not content_items:
        raise ValueError("MCP tool result has empty content")
    item = content_items[0]
    text = item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text


# ---------------------------------------------------------------------------
# Login flow (browser-based initial OAuth)
# ---------------------------------------------------------------------------

async def _local_callback_server(port: int = 0) -> tuple[int, asyncio.Future]:
    """Start a local HTTP server to receive the OAuth redirect. Returns (port, future)."""
    loop = asyncio.get_event_loop()
    future: asyncio.Future[tuple[str, str | None]] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        code: str | None = None
        state: str | None = None
        try:
            data = await reader.read(4096)
            first_line = data.decode(errors="replace").split("\n")[0]
            path = first_line.split(" ")[1] if " " in first_line else ""
            params = parse_qs(urlparse(path).query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            body = b"<html><body><h1>Login complete. You can close this tab.</h1></body></html>"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            if not future.done():
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
            await future

    asyncio.create_task(_serve_until_done())
    return actual_port, future


async def login(
    server_name: str,
    creds_path: Path = DEFAULT_CREDS_PATH,
    *,
    url: str | None = None,
    client_name: str | None = None,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
) -> None:
    """Full browser-based OAuth login for server_name. Caches tokens + token_endpoint.

    url/client_name: inline overrides (no config entry needed).
    config_path: read the server registry from this file instead of the default search.
    """
    _servers = servers(config_path=config_path)
    server_url = url or _servers.get(server_name)
    if server_url is None:
        raise ValueError(
            f"Unknown server {server_name!r}. Pass --url or add it to config. "
            f"Known: {list(_servers)}"
        )

    storage = FileTokenStorage(server_name, creds_path, backend=resolve_cred_backend(cred_backend))

    # Stash the existing entry before clearing it. If the OAuth flow fails for
    # any reason (user cancel, network error, bad registration), we restore it
    # so the caller is not locked out of a previously-working server.
    data = storage._load()
    stashed = data.pop(server_name, None)
    storage._save(data)

    try:
        port, callback_future = await _local_callback_server()
        callback_uri = f"http://localhost:{port}/callback"

        async def redirect_handler(url: str) -> None:
            print(f"\nOpening browser: {url}\n")
            webbrowser.open(url)

        async def callback_handler() -> tuple[str, str | None]:
            print("Waiting for OAuth callback… (complete login in your browser)")
            return await callback_future

        provider = OAuthClientProvider(
            server_url=server_url,
            client_metadata=OAuthClientMetadata(
                client_name=client_name or _resolve_client_name(server_name),
                redirect_uris=[callback_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            ),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

        try:
            async with _open_http(server_url, auth=provider) as (read, write, _):
                async with ClientSession(read, write) as s:
                    await s.initialize()

                    # Persist the token endpoint for later pre-flight refresh. Do this
                    # independently of any tool call — not every server exposes `whoami`.
                    if provider.context.oauth_metadata is not None:
                        endpoint_url = str(provider.context.oauth_metadata.token_endpoint)
                        creds_data = storage._load()
                        creds_data.setdefault(server_name, {})["token_endpoint"] = endpoint_url
                        storage._save(creds_data)

                    # Confirm the authenticated session works with a server-agnostic
                    # call. `list_tools` is part of the MCP protocol — every server
                    # supports it, unlike any specific tool name.
                    tools = await s.list_tools()
                    print(f"Login OK ({server_name}); {len(tools.tools)} tool(s) available")
        finally:
            if not callback_future.done():
                callback_future.set_result((None, None))
            await asyncio.sleep(0)

    except BaseException:
        # Restore the stashed credential so a failed login attempt doesn't lock
        # the user out of a previously-working server.
        if stashed is not None:
            restore = storage._load()
            restore[server_name] = stashed
            storage._save(restore)
        raise

    print(f"Credentials saved to {creds_path}")


async def ensure_login(
    server_name: str,
    creds_path: Path = DEFAULT_CREDS_PATH,
    *,
    url: str | None = None,
    client_name: str | None = None,
    config_path: str | Path | None = None,
    cred_backend: str | None = None,
) -> None:
    """Ensure a usable token exists for server_name, refreshing or logging in.

    Silent when a valid (or refreshable) token is cached — runs the same
    pre-flight refresh as a normal call. Opens the browser via login() only when
    there is no token, or the refresh token itself is gone/expired. Idempotent:
    safe to call before every run.

    Cases:
    - Fresh token: no-op.
    - Near/past expiry, refresh_token present: silent out-of-band renewal.
    - Near/past expiry, no refresh_token (or renewal fails): browser login.
    - No token at all: browser login.
    """
    storage = FileTokenStorage(
        server_name, creds_path, backend=resolve_cred_backend(cred_backend)
    )
    try:
        await _pre_flight_refresh(server_name, storage)
    except ReauthenticationRequired:
        await login(server_name, creds_path, url=url, client_name=client_name,
                    config_path=config_path, cred_backend=cred_backend)
        return
    if await storage.get_tokens() is None:   # first-time: no token cached at all
        await login(server_name, creds_path, url=url, client_name=client_name,
                    config_path=config_path, cred_backend=cred_backend)
