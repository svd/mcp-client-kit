"""Unit tests for _bridge transport routing.

Tests the bearer-token path in session() and McpBridgeCaller without making
real network connections. We mock _open_http and patch the internal
async context-manager helpers so routing logic is exercised in pure Python.

Async helpers are invoked via asyncio.run() (matching the project convention —
no pytest-asyncio dependency needed).
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import warnings
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    """_bearer_session must call _open_http with Authorization: Bearer <tok>."""
    captured: dict = {}

    @asynccontextmanager
    async def fake_http(url, headers=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        read, write = MagicMock(), MagicMock()
        yield read, write, None

    mock_s = _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge._open_http", fake_http), \
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
        with patch("mcp_client_kit._bridge._open_http", fake_http), \
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
    async def fake_oauth(name, url, *, client_name=None, cred_backend=None):
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
    async def fake_oauth(name, url, *, client_name=None, cred_backend=None):
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


# ---------------------------------------------------------------------------
# session() raw URL path: no auth, no config entry
# ---------------------------------------------------------------------------

def test_session_raw_url_uses_open_http_with_no_auth():
    """session(raw_url) with no bearer/OAuth must call _open_http with no headers or auth."""
    captured: dict = {}

    @asynccontextmanager
    async def fake_open_http(url, *, headers=None, auth=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["auth"] = auth
        read, write = MagicMock(), MagicMock()
        yield read, write, None

    mock_s = _make_mock_session()

    async def run():
        with patch("mcp_client_kit._bridge._open_http", fake_open_http), \
             patch("mcp_client_kit._bridge.ClientSession") as mock_cs, \
             patch("mcp_client_kit._bridge.servers", return_value={}):
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_s)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)
            async with _bridge.session("https://public.example.com/mcp"):
                pass

    asyncio.run(run())
    assert captured["url"] == "https://public.example.com/mcp"
    assert captured["headers"] is None, "unauthenticated path must not inject headers"
    assert captured["auth"] is None, "unauthenticated path must not inject auth"


# ---------------------------------------------------------------------------
# FileTokenStorage: file permissions and atomic write
# ---------------------------------------------------------------------------

def test_file_storage_sets_0600_file_and_0700_dir(tmp_path):
    """_file_save must create credentials with 0600 and parent dir with 0700."""
    from mcp.shared.auth import OAuthToken
    creds = tmp_path / "subdir" / "credentials.json"
    storage = _bridge.FileTokenStorage("s", credentials_path=creds, backend="file")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="tok", token_type="bearer")))
    assert creds.exists()
    assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600, "file must be 0600"
    assert stat.S_IMODE(os.stat(creds.parent).st_mode) == 0o700, "dir must be 0700"


def test_file_storage_round_trip(tmp_path):
    """Tokens saved by file backend round-trip to a fresh storage instance."""
    from mcp.shared.auth import OAuthToken
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("s", credentials_path=creds, backend="file")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="mytoken", token_type="bearer")))
    storage2 = _bridge.FileTokenStorage("s", credentials_path=creds, backend="file")
    loaded = asyncio.run(storage2.get_tokens())
    assert loaded is not None
    assert loaded.access_token == "mytoken"


def test_file_storage_self_heals_loose_permissions(tmp_path):
    """_file_load must chmod a world-readable file to 0600 and emit a warning."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({}))
    os.chmod(creds, 0o644)
    storage = _bridge.FileTokenStorage("s", credentials_path=creds, backend="file")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        storage._file_load()
    assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600, "perms must be fixed to 0600"
    assert any("0644" in str(w.message) or "fixed" in str(w.message) for w in caught), \
        "must warn about loose permissions"


# ---------------------------------------------------------------------------
# resolve_cred_backend: precedence order
# ---------------------------------------------------------------------------

def test_resolve_cred_backend_default_is_file(monkeypatch):
    """Without any input, resolve_cred_backend returns 'file'."""
    monkeypatch.delenv(_bridge._CRED_BACKEND_ENV, raising=False)
    with patch("mcp_client_kit._bridge._load_client_config", return_value={}):
        assert _bridge.resolve_cred_backend(None) == "file"


def test_resolve_cred_backend_cli_beats_env(monkeypatch):
    """CLI arg beats env var."""
    monkeypatch.setenv(_bridge._CRED_BACKEND_ENV, "keyring")
    assert _bridge.resolve_cred_backend("file") == "file"


def test_resolve_cred_backend_env_beats_config(monkeypatch):
    """Env var beats config file."""
    monkeypatch.setenv(_bridge._CRED_BACKEND_ENV, "keyring")
    with patch("mcp_client_kit._bridge._load_client_config", return_value={"cred_backend": "auto"}):
        assert _bridge.resolve_cred_backend(None) == "keyring"


def test_resolve_cred_backend_config_file(monkeypatch, tmp_path):
    """Config file cred_backend key is used when no CLI arg or env var."""
    monkeypatch.delenv(_bridge._CRED_BACKEND_ENV, raising=False)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"cred_backend": "auto"}))
    with patch("mcp_client_kit._bridge.DEFAULT_CONFIG_PATH", cfg):
        assert _bridge.resolve_cred_backend(None) == "auto"


def test_resolve_cred_backend_unknown_raises():
    """Unknown backend value must raise ValueError."""
    with pytest.raises(ValueError, match="Unknown"):
        _bridge.resolve_cred_backend("s3")


# ---------------------------------------------------------------------------
# _load_client_config
# ---------------------------------------------------------------------------

def test_load_client_config_absent_returns_empty(tmp_path):
    """_load_client_config returns {} when the config file does not exist."""
    assert _bridge._load_client_config(tmp_path / "no-config.json") == {}


def test_load_client_config_reads_key(tmp_path):
    """_load_client_config returns the parsed JSON dict."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"cred_backend": "keyring", "other": 1}))
    data = _bridge._load_client_config(cfg)
    assert data["cred_backend"] == "keyring"


# ---------------------------------------------------------------------------
# Keyring backend: fake in-memory store + no-backend fallback
# ---------------------------------------------------------------------------

class _FakeKeyring:
    """In-memory keyring stub that mimics keyring module's interface."""
    def __init__(self):
        self._store: dict = {}
        self.set_calls: list = []
        self.get_calls: list = []

    def get_password(self, service, username):
        self.get_calls.append((service, username))
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self.set_calls.append((service, username))
        self._store[(service, username)] = password


def test_keyring_backend_round_trip(tmp_path):
    """keyring backend stores/loads tokens via the injected fake keyring."""
    from mcp.shared.auth import OAuthToken
    fake_kr = _FakeKeyring()
    creds = tmp_path / "credentials.json"

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        storage = _bridge.FileTokenStorage("srv", credentials_path=creds, backend="keyring")
        asyncio.run(storage.set_tokens(OAuthToken(access_token="kr_token", token_type="bearer")))
        assert not creds.exists(), "keyring backend must not write to the file"
        assert fake_kr.set_calls, "set_password must have been called on the fake keyring"
        storage2 = _bridge.FileTokenStorage("srv", credentials_path=creds, backend="keyring")
        loaded = asyncio.run(storage2.get_tokens())
        assert fake_kr.get_calls, "get_password must have been called on the fake keyring"

    assert loaded is not None
    assert loaded.access_token == "kr_token"


def test_keyring_backend_falls_back_to_file_when_unavailable(tmp_path):
    """When keyring raises on set_password, falls back to hardened file + warns."""
    from mcp.shared.auth import OAuthToken

    class _BrokenKeyring:
        def get_password(self, s, u): raise RuntimeError("no keyring")
        def set_password(self, s, u, p): raise RuntimeError("no keyring")

    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("srv", credentials_path=creds, backend="keyring")

    with patch.dict("sys.modules", {"keyring": _BrokenKeyring()}), \
         warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(storage.set_tokens(OAuthToken(access_token="fb_token", token_type="bearer")))

    assert creds.exists(), "fallback must write to file"
    assert any("keyring" in str(w.message).lower() for w in caught), "must warn on fallback"
    assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600, "fallback file must be 0600"


# ---------------------------------------------------------------------------
# parse() — JSON, repr, and plain-text payloads  (#4)
# ---------------------------------------------------------------------------

def _item(text: str) -> dict:
    return {"type": "text", "text": text}


def test_parse_json_dict():
    """Standard JSON dict payload is parsed to a Python dict."""
    result = _bridge.parse([_item('{"name": "Alice", "id": 1}')])
    assert result == {"name": "Alice", "id": 1}


def test_parse_json_list():
    """Standard JSON list payload is parsed to a Python list."""
    result = _bridge.parse([_item('[{"name": "users"}, {"name": "orders"}]')])
    assert result == [{"name": "users"}, {"name": "orders"}]


def test_parse_python_repr_dict():
    """Python repr()-formatted single-quoted dict is parsed via ast.literal_eval."""
    result = _bridge.parse([_item("[{'name': 'users'}, {'name': 'orders'}]")])
    assert result == [{"name": "users"}, {"name": "orders"}]
    assert isinstance(result, list), "repr payload must parse to a list, not str"


def test_parse_python_repr_nested():
    """Nested Python repr() structure is parsed correctly."""
    result = _bridge.parse([_item("{'tables': [{'name': 'users'}]}")])
    assert result == {"tables": [{"name": "users"}]}


def test_parse_plain_text_fallback():
    """Non-JSON non-repr plain text falls back to str."""
    result = _bridge.parse([_item("OK")])
    assert result == "OK"


def test_parse_empty_content_raises():
    """Empty content list raises ValueError."""
    with pytest.raises(ValueError, match="empty"):
        _bridge.parse([])


def test_parse_repr_not_exec_unsafe():
    """ast.literal_eval does not execute arbitrary expressions — rejects code."""
    result = _bridge.parse([_item("__import__('os').system('true')")])
    # Must fall back to str, not execute.
    assert isinstance(result, str)
