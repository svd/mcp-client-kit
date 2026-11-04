"""Non-interactive credential checks: ensure_login(interactive=False) and the SDK's error log."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest

from mcpgen import _bridge

if sys.version_info < (3, 11):  # pragma: no cover - version-gated
    from exceptiongroup import BaseExceptionGroup


def _seed(path, tokens: dict | None, **entry) -> None:
    data = {"acme": {**entry, **({"tokens": tokens} if tokens is not None else {})}}
    _bridge.FileTokenStorage("acme", path)._save(data)


def _ensure(path, **kwargs) -> None:
    asyncio.run(_bridge.ensure_login("acme", path, url="https://mcp.test/mcp", cred_backend="file", **kwargs))


def test_non_interactive_with_no_token_raises_instead_of_logging_in(tmp_path):
    creds = tmp_path / "c.json"
    with patch.object(_bridge, "login", AsyncMock()) as login:
        with pytest.raises(_bridge.ReauthenticationRequired, match="acme"):
            _ensure(creds, interactive=False)
    login.assert_not_called()


def test_non_interactive_with_a_dead_refresh_token_raises_instead_of_logging_in(tmp_path):
    creds = tmp_path / "c.json"
    _seed(
        creds, {"access_token": "a", "token_type": "Bearer", "refresh_token": "r", "expires_at": int(time.time()) - 10}
    )
    dead = AsyncMock(side_effect=_bridge.ReauthenticationRequired("Token refresh failed (400, invalid_grant)"))
    with patch.object(_bridge, "_pre_flight_refresh", dead), patch.object(_bridge, "login", AsyncMock()) as login:
        with pytest.raises(_bridge.ReauthenticationRequired, match="invalid_grant"):
            _ensure(creds, interactive=False)
    login.assert_not_called()


def test_non_interactive_with_a_live_token_is_silent(tmp_path):
    creds = tmp_path / "c.json"
    _seed(creds, {"access_token": "a", "token_type": "Bearer", "expires_at": int(time.time()) + 3600})
    with patch.object(_bridge, "login", AsyncMock()) as login:
        _ensure(creds, interactive=False)
    login.assert_not_called()


def test_non_interactive_lets_an_unavailable_refresh_propagate(tmp_path):
    creds = tmp_path / "c.json"
    down = AsyncMock(side_effect=_bridge.TokenRefreshUnavailable("503"))
    with patch.object(_bridge, "_pre_flight_refresh", down), patch.object(_bridge, "login", AsyncMock()) as login:
        with pytest.raises(_bridge.TokenRefreshUnavailable):
            _ensure(creds, interactive=False)
    login.assert_not_called()


def test_interactive_default_still_logs_in(tmp_path):
    creds = tmp_path / "c.json"
    with (
        patch.object(_bridge, "login", AsyncMock()) as login,
        patch.object(_bridge, "_verify_login_took", lambda *a: None),
    ):
        _ensure(creds)
    login.assert_awaited_once()


def test_ensure_login_all_forwards_interactive(tmp_path):
    creds = tmp_path / "c.json"
    with patch.object(_bridge, "ensure_login", AsyncMock()) as one:
        asyncio.run(_bridge.ensure_login_all(["a", "b"], creds, interactive=False))
    assert [c.kwargs["interactive"] for c in one.await_args_list] == [False, False]


# ── The SDK's "OAuth flow error" log ─────────────────────────────────────────


def _sdk_log(exc: BaseException) -> None:
    logger = logging.getLogger("mcp.client.auth.oauth2")
    try:
        raise exc
    except BaseException:
        logger.exception("OAuth flow error")


def test_reauthentication_required_is_kept_out_of_the_sdk_error_log(caplog):
    caplog.set_level(logging.DEBUG)
    _sdk_log(_bridge.ReauthenticationRequired("OAuth re-auth needed for 'acme'"))
    assert not [r for r in caplog.records if r.name == "mcp.client.auth.oauth2"]


def test_other_oauth_flow_errors_are_still_logged(caplog):
    caplog.set_level(logging.DEBUG)
    _sdk_log(RuntimeError("registration endpoint 500"))
    assert [r for r in caplog.records if r.name == "mcp.client.auth.oauth2"]


def test_a_dead_credential_in_a_real_sdk_flow_logs_no_traceback(tmp_path, monkeypatch, caplog):
    """Drive the SDK's own auth flow to the `_no_browser` raise and check its log stays clean."""
    import httpx

    def server(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/mcp":
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.test/.well-known/oauth-protected-resource/mcp"'
                },
            )
        if path.startswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(
                200, json={"resource": "https://mcp.test/mcp", "authorization_servers": ["https://as.test"]}
            )
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://as.test",
                    "authorization_endpoint": "https://as.test/authorize",
                    "token_endpoint": "https://as.test/token",
                    "response_types_supported": ["code"],
                },
            )
        return httpx.Response(404)

    real_factory = _bridge.create_mcp_http_client

    def factory(headers=None, auth=None, **kwargs):
        client = real_factory(headers=headers, auth=auth, **kwargs)
        client._transport = httpx.MockTransport(server)
        return client

    monkeypatch.setattr(_bridge, "create_mcp_http_client", factory)
    creds = tmp_path / "c.json"
    _seed(
        creds,
        {"access_token": "revoked", "token_type": "Bearer", "expires_at": int(time.time()) + 3600},
        client_info={"client_id": "cid", "redirect_uris": ["http://localhost:1/callback"]},
    )
    caplog.set_level(logging.DEBUG)

    async def open_session():
        async with _bridge._http_session("acme", "https://mcp.test/mcp", creds_path=creds, cred_backend="file"):
            pass  # pragma: no cover

    with pytest.raises(BaseException) as info:
        asyncio.run(asyncio.wait_for(open_session(), 10))

    leaves = info.value.exceptions if isinstance(info.value, BaseExceptionGroup) else [info.value]
    assert any(isinstance(e, _bridge.ReauthenticationRequired) for e in leaves)
    assert not [r for r in caplog.records if r.exc_info]
    assert not [r for r in caplog.records if r.name == "mcp.client.auth.oauth2" and r.levelno >= logging.WARNING]
