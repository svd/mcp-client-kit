"""Unit tests for _bridge transport routing.

Tests the bearer-token path in session() and McpBridgeCaller without making
real network connections. We mock streamablehttp_client and patch the internal
async context-manager helpers so routing logic is exercised in pure Python.

Async helpers are invoked via asyncio.run() (matching the project convention —
no pytest-asyncio dependency needed).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_client_kit import _bridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_session(tool_response: dict | None = None):
    """Return a mock MCP ClientSession that records calls."""
    s = MagicMock()
    s.initialize = AsyncMock()
    if tool_response is not None:
        import json
        payload = json.dumps(tool_response)
        s.call_tool = AsyncMock(return_value=MagicMock(
            content=[MagicMock(type="text", text=payload)]
        ))
    return s


# ---------------------------------------------------------------------------
# _bearer_session: header and no-file-IO checks
# ---------------------------------------------------------------------------

def test_bearer_session_passes_authorization_header():
    """_bearer_session must call streamablehttp_client with Authorization: Bearer <tok>."""
    captured: dict = {}

    @asynccontextmanager
    async def fake_http(url, headers=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        read, write = MagicMock(), MagicMock()
        yield read, write, None

    mock_s = _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge.streamablehttp_client", fake_http), \
             patch("mcp_client_kit._bridge.ClientSession") as mock_cs:
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_s)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)
            async with _bridge._bearer_session("https://api.example.com/mcp", "tok_abc"):
                pass

    asyncio.run(run())
    assert captured["headers"] == {"Authorization": "Bearer tok_abc"}
    assert captured["url"] == "https://api.example.com/mcp"


def test_bearer_session_does_not_touch_file_storage(tmp_path):
    """_bearer_session must not create or modify any credentials file."""
    creds = tmp_path / "credentials.json"

    @asynccontextmanager
    async def fake_http(url, headers=None, **kwargs):
        read, write = MagicMock(), MagicMock()
        yield read, write, None

    mock_s = _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge.streamablehttp_client", fake_http), \
             patch("mcp_client_kit._bridge.ClientSession") as mock_cs, \
             patch("mcp_client_kit._bridge.DEFAULT_CREDS_PATH", creds):
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_s)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)
            async with _bridge._bearer_session("https://api.example.com/mcp", "tok"):
                pass

    asyncio.run(run())
    assert not creds.exists(), "bearer path must not create a credentials file"


# ---------------------------------------------------------------------------
# session() routing: bearer takes precedence over OAuth
# ---------------------------------------------------------------------------

def test_session_routes_bearer_not_oauth_when_bearer_provided():
    """session(bearer=…) uses _bearer_session; _http_session must not be called."""
    bearer_calls: list = []
    oauth_calls: list = []

    @asynccontextmanager
    async def fake_bearer(url, tok):
        bearer_calls.append((url, tok))
        yield _make_mock_session()

    @asynccontextmanager
    async def fake_oauth(name, url, *, client_name=None):
        oauth_calls.append(name)
        yield _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge._bearer_session", fake_bearer), \
             patch("mcp_client_kit._bridge._http_session", fake_oauth), \
             patch("mcp_client_kit._bridge.servers", return_value={}):
            async with _bridge.session(
                "github",
                url="https://api.githubcopilot.com/mcp/",
                bearer="ghp_test",
            ):
                pass

    asyncio.run(run())
    assert bearer_calls == [("https://api.githubcopilot.com/mcp/", "ghp_test")]
    assert oauth_calls == [], "OAuth path must not be called when bearer is set"


def test_session_bearer_uses_server_arg_as_url_when_no_url():
    """When bearer is set but url is absent, session uses the server arg as the URL."""
    bearer_calls: list = []

    @asynccontextmanager
    async def fake_bearer(url, tok):
        bearer_calls.append(url)
        yield _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge._bearer_session", fake_bearer), \
             patch("mcp_client_kit._bridge.servers", return_value={}):
            async with _bridge.session(
                "https://api.githubcopilot.com/mcp/",
                bearer="ghp_test",
            ):
                pass

    asyncio.run(run())
    assert bearer_calls == ["https://api.githubcopilot.com/mcp/"]


def test_session_oauth_path_unchanged_without_bearer():
    """When bearer is absent, session still routes to _http_session for known server names."""
    oauth_calls: list = []

    @asynccontextmanager
    async def fake_oauth(name, url, *, client_name=None):
        oauth_calls.append(name)
        yield _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge._http_session", fake_oauth), \
             patch("mcp_client_kit._bridge.servers",
                   return_value={"myserver": "https://mcp.example.com/mcp"}):
            async with _bridge.session("myserver"):
                pass

    asyncio.run(run())
    assert oauth_calls == ["myserver"]


# ---------------------------------------------------------------------------
# McpBridgeCaller: bearer wired through to session()
# ---------------------------------------------------------------------------

def test_mcp_bridge_caller_threads_bearer_to_session():
    """McpBridgeCaller(bearer=…).call() must forward the bearer kwarg to session()."""
    session_kwargs: dict = {}

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        session_kwargs.update(kwargs)
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcp_client_kit._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(
                url="https://api.githubcopilot.com/mcp/",
                bearer="ghp_unit_test",
            )
            return await caller.call("github", "get_me", {})

    result = asyncio.run(run())
    assert session_kwargs.get("bearer") == "ghp_unit_test"
    assert result == {"ok": True}


def test_mcp_bridge_caller_bearer_none_by_default():
    """McpBridgeCaller with no bearer= must pass bearer=None (not absent key) to session."""
    session_kwargs: dict = {}

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        session_kwargs.update(kwargs)
        yield _make_mock_session({"x": 1})

    async def run():
        with patch("mcp_client_kit._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="echo hi")
            return await caller.call("s", "t", {})

    asyncio.run(run())
    assert "bearer" in session_kwargs
    assert session_kwargs["bearer"] is None
