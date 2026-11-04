"""Two-step OAuth login with a caller-owned redirect URI.

`begin_login()` discovers the authorization server, registers a client (or reuses one) and
returns a `PendingLogin`: the authorization URL to send the browser to, and everything the
code exchange needs. `complete_login()` takes that object — possibly in another process, via
`to_json()` / `from_json()` — and the URL the browser was redirected to, exchanges the code
and writes the credential entry `login()` would have written.

The round never touches the credential store until a token is in hand: discovery,
registration and the exchange all run against an in-memory `_StagingStorage`, and
`complete_login()` replaces the server's entry in one locked write. A pending login that is
abandoned, expires or fails therefore leaves the previous credential in place, with no
stash to restore.

SDK coupling. The flow is driven through `OAuthClientProvider.async_auth_flow` so discovery,
scope selection and registration behave exactly as in `login()`. Three private members are
used, all pinned by tests because `mcp` is bounded to a range rather than a version:
`_perform_authorization` is overridden to stop the flow at the authorization redirect,
`_exchange_token_authorization_code` builds the token request, and `_get_token_endpoint`
names the endpoint `_pre_flight_refresh` later renews against.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
from mcp.client.auth import OAuthClientProvider, OAuthRegistrationError, TokenStorage
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from mcp.types import LATEST_PROTOCOL_VERSION

from mcpgen._bridge import (
    _CALLBACK_TIMEOUT,
    DEFAULT_CREDS_PATH,
    FileTokenStorage,
    LoginWontHelp,
    PostLoginCheckFailed,
    _body_excerpt,
    _client_metadata,
    _describe,
    _explain_registration_error,
    _http_session,
    _load_store_for_login,
    _oauth_error_code,
    _redact_secret_text,
    _store_locks,
    _user_agent,
    _user_agent_hook,
    package_version,
    resolve_cred_backend,
    servers,
)

_PENDING_FORMAT = 1
"""Version of the `to_json()` document. `from_json()` refuses any other value, so a blob
written by a later mcpgen fails loudly instead of completing with a misread field."""


# ── Errors ───────────────────────────────────────────────────────────────────


class LoginNotCompleted(Exception):
    """A pending login ended without a token. The previous credential is untouched.

    Base of the two-step errors below; another `begin_login()` is the way forward from any
    of them.
    """


class LoginStateMismatch(LoginNotCompleted):
    """The callback's `state` is missing or belongs to a different pending login."""


class PendingLoginExpired(LoginNotCompleted):
    """The pending login is past its `expires_at`."""


class AuthorizationDenied(LoginNotCompleted):
    """The authorization server redirected back with `error=` instead of a code.

    `error` is the RFC 6749 §4.1.2.1 code (`access_denied` when the user declined);
    `error_description` is the server's text, or None when it sent none.
    """

    def __init__(self, error: str, error_description: str | None = None) -> None:
        self.error = error
        self.error_description = error_description
        detail = f" — {error_description}" if error_description else ""
        super().__init__(f"Authorization denied: {error}{detail}")


class CodeExchangeRejected(LoginNotCompleted):
    """The token endpoint did not turn the authorization code into a token.

    `status_code` is the HTTP status; `error` and `error_description` are the RFC 6749
    §5.2 members when the body carried them. A code is single-use, so retrying the same
    callback fails the same way.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error: str | None = None,
        error_description: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.error = error
        self.error_description = error_description
        super().__init__(message)


# ── Public data ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, repr=False)
class PendingLogin:
    """A login between `begin_login()` and `complete_login()`.

    `authorization_url` is where to send the browser, `state` the OAuth `state` parameter
    that identifies which pending login a callback belongs to, and `expires_at` the epoch
    second after which `complete_login()` refuses it.

    `to_json()` carries `code_verifier` and, for a confidential client, `client_secret`.
    Store the blob where only the server can read it — a server-side session or an
    encrypted cookie, never a plain cookie or a URL — and drop it once the login completes.
    `repr()` and `str()` print neither.
    """

    server_name: str
    server_url: str
    redirect_uri: str
    authorization_url: str
    state: str
    expires_at: float
    code_verifier: str
    creds_path: str
    cred_backend: str
    client_info: dict
    client_metadata: dict
    oauth_metadata: dict | None
    protected_resource_metadata: dict | None
    auth_server_url: str | None
    protocol_version: str | None

    def __repr__(self) -> str:
        return (
            f"PendingLogin(server_name={self.server_name!r}, redirect_uri={self.redirect_uri!r}, "
            f"state={self.state!r}, expires_at={self.expires_at!r})"
        )

    __str__ = __repr__

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_json(self) -> str:
        """Serialize for `from_json()` in this or another process. The text holds secrets."""
        return json.dumps({"format": _PENDING_FORMAT, **asdict(self)})

    @classmethod
    def from_json(cls, blob: str | bytes) -> PendingLogin:
        """Rebuild a pending login from `to_json()` output.

        Raises ValueError for text that is not a pending login of this format.
        """
        try:
            raw = json.loads(blob)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"not a pending login: {exc}") from None
        if not isinstance(raw, dict) or raw.get("format") != _PENDING_FORMAT:
            raise ValueError(f"not a pending login of format {_PENDING_FORMAT}")
        names = [f.name for f in fields(cls)]
        missing = [n for n in names if n not in raw]
        if missing:
            raise ValueError(f"pending login is missing {missing}")
        return cls(**{n: raw[n] for n in names})


@dataclass(frozen=True)
class LoginResult:
    """What `complete_login()` saved. `tools_count` is None unless `verify=True`."""

    server_name: str
    token_endpoint: str
    tools_count: int | None = None


def callback_state(callback_url: str) -> str | None:
    """The `state` parameter of a redirect URL: the key to look up its pending login by."""
    values = parse_qs(urlparse(callback_url).query).get("state")
    return values[0] if values else None


# ── Internals ────────────────────────────────────────────────────────────────


class _StagingStorage(TokenStorage):
    """In-memory TokenStorage the round runs against, so the real store stays untouched."""

    def __init__(self, client_info: OAuthClientInformationFull | None = None) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info = client_info

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class _AuthorizationReady(BaseException):
    """Carries the authorization request out of the SDK's auth flow.

    A `BaseException` on purpose: the SDK wraps the whole discovery-to-exchange block in
    `except Exception: logger.exception("OAuth flow error")`, and stopping there is the
    expected outcome, not an error to log with a traceback.
    """

    def __init__(self, authorization_url: str, state: str, code_verifier: str) -> None:
        super().__init__("authorization ready")
        self.authorization_url = authorization_url
        self.state = state
        self.code_verifier = code_verifier


class _BeginProvider(OAuthClientProvider):
    """Stops the SDK flow where it would redirect the browser, handing back the request.

    The authorization URL is built the way `_perform_authorization_code_grant` builds it
    in mcp 1.27 — the endpoint, the resource indicator and the scope rules — because that
    method keeps the PKCE verifier in a local and so cannot be reused for a round that
    completes elsewhere.
    """

    async def _perform_authorization(self) -> httpx.Request:
        ctx = self.context
        if ctx.client_info is None or not ctx.client_metadata.redirect_uris:
            raise LoginWontHelp("the OAuth flow reached authorization without a client registration")
        if ctx.oauth_metadata and ctx.oauth_metadata.authorization_endpoint:
            endpoint = str(ctx.oauth_metadata.authorization_endpoint)
        else:
            endpoint = urljoin(ctx.get_authorization_base_url(ctx.server_url), "/authorize")
        pkce = PKCEParameters.generate()
        state = secrets.token_urlsafe(32)
        params = {
            "response_type": "code",
            "client_id": ctx.client_info.client_id,
            "redirect_uri": str(ctx.client_metadata.redirect_uris[0]),
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": "S256",
        }
        if ctx.should_include_resource_param(ctx.protocol_version):
            params["resource"] = ctx.get_resource_url()  # RFC 8707
        if ctx.client_metadata.scope:
            params["scope"] = ctx.client_metadata.scope
        raise _AuthorizationReady(f"{endpoint}?{urlencode(params)}", state, pkce.code_verifier)


@asynccontextmanager
async def _oauth_client() -> AsyncIterator[httpx.AsyncClient]:
    """The HTTP client both steps talk to the MCP and authorization servers through.

    Same User-Agent treatment as `_open_http`: the auth flow hand-builds its requests, so
    only the request hook reaches them.
    """
    user_agent = _user_agent()
    async with create_mcp_http_client(headers={"User-Agent": user_agent}) as client:
        client.event_hooks = {
            **client.event_hooks,
            "request": [*client.event_hooks.get("request", []), _user_agent_hook(user_agent)],
        }
        yield client


def _bounded(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Request:
    """Give *request* the client's timeout. Returns *request*.

    `client.send` applies no timeout of its own: only requests built by the client carry
    one, and the probe and every request the SDK's auth flow yields are built bare. Unbounded,
    a server that never answers would hold a web request handler forever.
    """
    request.extensions.setdefault("timeout", client.timeout.as_dict())
    return request


def _probe_request(server_url: str) -> httpx.Request:
    """An unauthenticated MCP `initialize`: the request whose 401 starts discovery."""
    body = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcpgen", "version": package_version()},
        },
    }
    return httpx.Request(
        "POST",
        server_url,
        json=body,
        headers={"Accept": "application/json, text/event-stream"},
    )


async def _drive_until_authorization(
    client: httpx.AsyncClient, provider: _BeginProvider, server_url: str
) -> _AuthorizationReady:
    """Run the provider's auth flow over *client* until it is ready to redirect the browser.

    Returns the `_AuthorizationReady` it stopped with. Raises LoginWontHelp when the MCP
    server answers the probe without asking for authorization.
    """
    probe = _probe_request(server_url)
    flow = provider.async_auth_flow(probe)
    try:
        request = await flow.__anext__()
        while True:
            response = await client.send(_bounded(client, request), stream=True)
            try:
                # The probe's own success may be an event stream that stays open; nothing in
                # it is needed, and reading it could block. Every other response here is
                # discovery, registration or a 401/403 the flow parses.
                if not (request is probe and response.is_success):
                    await response.aread()
            finally:
                await response.aclose()
            try:
                request = await flow.asend(response)
            except StopAsyncIteration:
                raise LoginWontHelp(
                    f"{server_url} answered {response.status_code} without asking for OAuth "
                    f"authorization, so there is nothing to log in to."
                ) from None
    except _AuthorizationReady as ready:
        return ready
    finally:
        await flow.aclose()


def _validate_redirect_uri(redirect_uri: str) -> None:
    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not (parsed.netloc or parsed.path):
        raise ValueError(f"redirect_uri must be an absolute URL, got {redirect_uri!r}")
    if parsed.fragment:
        # RFC 6749 §3.1.2: the redirection endpoint URI must not include a fragment.
        raise ValueError(f"redirect_uri must not carry a fragment, got {redirect_uri!r}")


def _resolve_server_url(server_name: str, url: str | None, config_path: str | Path | None) -> str:
    known = servers(config_path=config_path)
    server_url = url or known.get(server_name)
    if server_url is None:
        raise ValueError(f"Unknown server {server_name!r}. Pass url= or add it to config. Known: {list(known)}")
    return server_url


def _stored_entry(storage: FileTokenStorage, server_name: str) -> dict:
    """The server's current entry, or {} when the store is absent or unreadable.

    Read-only and best effort: `begin_login()` only consults it for a registration to
    reuse, and an unreadable store is `complete_login()`'s to deal with when it writes.
    The file is read directly rather than through `_file_load`, which repairs the file's
    mode: `begin_login()` changes nothing on disk.
    """
    try:
        if storage._backend == "file":
            if not storage._path.exists():
                return {}
            data = json.loads(storage._path.read_text(encoding="utf-8"))
        else:
            data = storage._load()
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    entry = data.get(server_name) if isinstance(data, dict) else None
    return entry if isinstance(entry, dict) else {}


def _reusable_registration(entry: dict, redirect_uri: str) -> OAuthClientInformationFull | None:
    """The stored client registration, when it is one `begin_login()` would have made itself.

    That is: registered for *redirect_uri*, as a public client, and not past its
    `client_secret_expires_at`. A confidential registration from an older mcpgen fails the
    second test on purpose — reusing it brings back the two-authentication-methods token
    request `_client_metadata` registers as a public client to avoid.
    """
    raw = entry.get("client_info") or {}
    if not raw.get("client_id") or redirect_uri not in (raw.get("redirect_uris") or []):
        return None
    if raw.get("token_endpoint_auth_method") != "none":
        return None
    expires = raw.get("client_secret_expires_at")
    if expires and time.time() >= expires:
        return None
    try:
        return OAuthClientInformationFull.model_validate(raw)
    except Exception:  # noqa: BLE001 — a hand-edited entry that no longer validates is just not reusable
        return None


def _dump(model: Any) -> dict | None:
    return None if model is None else model.model_dump(mode="json", exclude_none=True)


# ── Public API ───────────────────────────────────────────────────────────────


async def begin_login(
    server_name: str,
    *,
    redirect_uri: str,
    creds_path: Path | str = DEFAULT_CREDS_PATH,
    url: str | None = None,
    client_name: str | None = None,
    cred_backend: str | None = None,
    config_path: str | Path | None = None,
    ttl: float | None = None,
    reuse_registration: bool = True,
) -> PendingLogin:
    """Start an OAuth login whose redirect lands on *redirect_uri*; no browser, no listener.

    Runs discovery and, unless a registration can be reused, Dynamic Client Registration
    for *redirect_uri*, then returns the `PendingLogin` to send the user on with. The
    credential store is read, never written: the server's current entry stays usable
    until `complete_login()` replaces it.

    redirect_uri: any absolute URL — host, port and path are the caller's.
    creds_path / cred_backend: the store `complete_login()` will write to, recorded in
        the pending login.
    url / client_name / config_path: as in `login()`.
    ttl: seconds until the pending login expires; None uses 300 (the same bound as
        `login()`'s callback wait). Must be positive.
    reuse_registration: reuse the stored client registration when its `redirect_uris`
        holds *redirect_uri* and the authorization server has not moved. False always
        registers anew.

    Raises OAuthRegistrationError when registration is refused, and LoginWontHelp when
    the server does not ask for OAuth at all.
    """
    _validate_redirect_uri(redirect_uri)
    lifetime = _CALLBACK_TIMEOUT if ttl is None else ttl
    if lifetime <= 0:
        raise ValueError(f"ttl must be positive, got {ttl!r}")
    server_url = _resolve_server_url(server_name, url, config_path)
    path = Path(creds_path)
    storage = FileTokenStorage(server_name, path, backend=resolve_cred_backend(cred_backend))
    backend = storage._backend  # concrete ("auto" resolved), before a keyring fallback can flip it

    metadata = _client_metadata(server_name, redirect_uri, client_name)
    canonical_uri = str(metadata.redirect_uris[0])  # type: ignore[index]  # set just above
    entry = _stored_entry(storage, server_name)
    reused = _reusable_registration(entry, canonical_uri) if reuse_registration else None

    async with _oauth_client() as client:
        staging = _StagingStorage(client_info=reused)
        provider = _BeginProvider(server_url=server_url, client_metadata=metadata, storage=staging)
        try:
            ready = await _drive_until_authorization(client, provider, server_url)
            # A registration belongs to the authorization server that issued it. When the
            # server's URL was overridden or its AS moved, the stored client_id means nothing
            # to the AS just discovered, so register there instead.
            stored_endpoint = entry.get("token_endpoint")
            if reused is not None and stored_endpoint and provider._get_token_endpoint() != stored_endpoint:
                metadata = _client_metadata(server_name, redirect_uri, client_name)
                staging = _StagingStorage()
                provider = _BeginProvider(server_url=server_url, client_metadata=metadata, storage=staging)
                ready = await _drive_until_authorization(client, provider, server_url)
        except OAuthRegistrationError as exc:
            # `from None`: the explained text is redacted; the original may quote a secret.
            raise _explain_registration_error(exc) from None

    ctx = provider.context
    assert ctx.client_info is not None  # _BeginProvider refuses to stop without one
    return PendingLogin(
        server_name=server_name,
        server_url=server_url,
        redirect_uri=canonical_uri,
        authorization_url=ready.authorization_url,
        state=ready.state,
        expires_at=time.time() + lifetime,
        code_verifier=ready.code_verifier,
        creds_path=str(path),
        cred_backend=backend,
        client_info=_dump(ctx.client_info) or {},
        client_metadata=_dump(ctx.client_metadata) or {},
        oauth_metadata=_dump(ctx.oauth_metadata),
        protected_resource_metadata=_dump(ctx.protected_resource_metadata),
        auth_server_url=ctx.auth_server_url,
        protocol_version=ctx.protocol_version,
    )


def _read_callback(pending: PendingLogin, callback_url: str) -> str:
    """Validate the redirect against *pending* and return its authorization code.

    No message here quotes *callback_url*: it carries the code.
    """
    params = parse_qs(urlparse(callback_url).query)

    def first(key: str) -> str | None:
        values = params.get(key)
        return values[0] if values else None

    state, error = first("state"), first("error")
    # A state that names another login is checked before the error it may carry: an error
    # redirect for someone else's login says nothing about this one.
    if state is not None and not secrets.compare_digest(state.encode(), pending.state.encode()):
        raise LoginStateMismatch(f"The callback's state does not belong to this pending login ({pending.server_name}).")
    # RFC 9207 §2.4: a callback naming an issuer other than the one this login was started
    # against is a mix-up, whatever its state says.
    issuer = first("iss")
    expected_issuer = (pending.oauth_metadata or {}).get("issuer")
    if issuer is not None and expected_issuer and issuer.rstrip("/") != str(expected_issuer).rstrip("/"):
        raise LoginStateMismatch(
            f"The callback names issuer {issuer!r}, not {expected_issuer!r} ({pending.server_name})."
        )
    if error:
        raise AuthorizationDenied(error, first("error_description"))
    if state is None:
        raise LoginStateMismatch(f"The callback carries no state parameter ({pending.server_name}).")
    code = first("code")
    if not code:
        raise LoginNotCompleted(f"The callback carries neither a code nor an error ({pending.server_name}).")
    return code


def _restore_provider(pending: PendingLogin, staging: _StagingStorage) -> OAuthClientProvider:
    """An SDK provider whose context is the one `begin_login()` stopped with."""
    client_info = OAuthClientInformationFull.model_validate(pending.client_info)
    staging.client_info = client_info
    provider = OAuthClientProvider(
        server_url=pending.server_url,
        client_metadata=OAuthClientMetadata.model_validate(pending.client_metadata),
        storage=staging,
    )
    ctx = provider.context
    ctx.client_info = client_info
    if pending.oauth_metadata is not None:
        ctx.oauth_metadata = OAuthMetadata.model_validate(pending.oauth_metadata)
    if pending.protected_resource_metadata is not None:
        ctx.protected_resource_metadata = ProtectedResourceMetadata.model_validate(pending.protected_resource_metadata)
    ctx.auth_server_url = pending.auth_server_url
    ctx.protocol_version = pending.protocol_version
    return provider


async def complete_login(pending: PendingLogin, callback_url: str, *, verify: bool = True) -> LoginResult:
    """Finish *pending* with the URL the authorization server redirected the browser to.

    Validates `state`, exchanges the code and replaces the server's credential entry —
    client registration, tokens and token endpoint — in one locked write to the store
    `begin_login()` recorded. That entry is the one `login()` writes. With *verify*, runs
    `list_tools()` over the new credential.

    Raises, leaving the previous credential in place:
        PendingLoginExpired — past `pending.expires_at`.
        LoginStateMismatch — the callback's `state` is missing or another login's.
        AuthorizationDenied — the callback carries `error=` (`access_denied`, …).
        CodeExchangeRejected — the token endpoint refused the code.
        LoginNotCompleted — the callback carries neither a code nor an error.
    Raises after saving the new credential:
        PostLoginCheckFailed — *verify* was set and `list_tools()` failed.
    """
    if pending.expired:
        raise PendingLoginExpired(f"The pending login for {pending.server_name} expired; start a new one.")
    code = _read_callback(pending, callback_url)

    staging = _StagingStorage()
    provider = _restore_provider(pending, staging)
    async with _oauth_client() as client:
        request = await provider._exchange_token_authorization_code(code, pending.code_verifier)
        response = await client.send(_bounded(client, request))
        if response.status_code != 200:
            raise _exchange_rejected(pending.server_name, response)
        try:
            await provider._handle_token_response(response)
        except Exception as exc:  # noqa: BLE001 — any parse failure means no token
            # `from None` and `_describe`: the SDK's message quotes the body it rejected.
            raise CodeExchangeRejected(
                f"The token endpoint for {pending.server_name} answered 200 without a usable token: {_describe(exc)}",
                status_code=response.status_code,
            ) from None
    assert staging.tokens is not None  # _handle_token_response stores before returning
    token_endpoint = provider._get_token_endpoint()

    entry = {
        "client_info": pending.client_info,
        "tokens": FileTokenStorage._serialize_tokens(staging.tokens),
        "token_endpoint": token_endpoint,
    }
    storage = FileTokenStorage(pending.server_name, Path(pending.creds_path), backend=pending.cred_backend)
    with _store_locks(storage._lock_backend, storage._path):
        data = _load_store_for_login(storage)
        data[pending.server_name] = entry
        storage._save(data)

    tools_count: int | None = None
    if verify:
        try:
            async with _http_session(
                pending.server_name,
                pending.server_url,
                cred_backend=pending.cred_backend,
                creds_path=Path(pending.creds_path),
            ) as session:
                tools_count = len((await session.list_tools()).tools)
        except Exception as exc:  # noqa: BLE001 — interrupts are BaseException and propagate
            raise PostLoginCheckFailed(
                f"Login succeeded ({pending.server_name}) and the token was saved, but the check that "
                f"follows it failed: {_describe(exc)}. Logging in again will not change this."
            ) from None
    return LoginResult(server_name=pending.server_name, token_endpoint=token_endpoint, tools_count=tools_count)


def _exchange_rejected(server_name: str, response: httpx.Response) -> CodeExchangeRejected:
    error = _oauth_error_code(response)
    description: str | None = None
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("error_description"), str):
            description = _redact_secret_text(body["error_description"])
    except ValueError:
        pass
    return CodeExchangeRejected(
        f"The token endpoint for {server_name} rejected the authorization code "
        f"({response.status_code}{', ' + error if error else ''}): {_body_excerpt(response)}",
        status_code=response.status_code,
        error=error,
        error_description=description,
    )
