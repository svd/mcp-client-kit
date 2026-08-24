"""Unit tests for _bridge transport routing.

Tests the bearer-token path in session() and McpBridgeCaller without making
real network connections. We mock _open_http and patch the internal
async context-manager helpers so routing logic is exercised in pure Python.

Async helpers are invoked via asyncio.run() (matching the project convention —
no pytest-asyncio dependency needed).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import stat
import time
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from mcpgen import _bridge

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
        s.call_tool = AsyncMock(return_value=MagicMock(content=[MagicMock(type="text", text=payload)]))
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
        with patch("mcpgen._bridge._open_http", fake_http), patch("mcpgen._bridge.ClientSession") as mock_cs:
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
        with (
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.ClientSession") as mock_cs,
            patch("mcpgen._bridge.DEFAULT_CREDS_PATH", creds),
        ):
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
    async def fake_oauth(name, url, *, client_name=None, cred_backend=None, creds_path=None):
        oauth_calls.append(name)
        yield _make_mock_session()

    async def run():
        with (
            patch("mcpgen._bridge._bearer_session", fake_bearer),
            patch("mcpgen._bridge._http_session", fake_oauth),
            patch("mcpgen._bridge.servers", return_value={}),
        ):
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
        with patch("mcpgen._bridge._bearer_session", fake_bearer), patch("mcpgen._bridge.servers", return_value={}):
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
    async def fake_oauth(name, url, *, client_name=None, cred_backend=None, creds_path=None):
        oauth_calls.append(name)
        yield _make_mock_session()

    async def run():
        with (
            patch("mcpgen._bridge._http_session", fake_oauth),
            patch("mcpgen._bridge.servers", return_value={"myserver": "https://mcp.example.com/mcp"}),
        ):
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
        with patch("mcpgen._bridge.session", fake_session):
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
        with patch("mcpgen._bridge.session", fake_session):
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
        with (
            patch("mcpgen._bridge._open_http", fake_open_http),
            patch("mcpgen._bridge.ClientSession") as mock_cs,
            patch("mcpgen._bridge.servers", return_value={}),
        ):
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
    assert any("0644" in str(w.message) or "fixed" in str(w.message) for w in caught), (
        "must warn about loose permissions"
    )


# ---------------------------------------------------------------------------
# resolve_cred_backend: precedence order
# ---------------------------------------------------------------------------


def test_resolve_cred_backend_default_is_file(monkeypatch):
    """Without any input, resolve_cred_backend returns 'file'."""
    monkeypatch.delenv(_bridge._CRED_BACKEND_ENV, raising=False)
    with patch("mcpgen._bridge._load_client_config", return_value={}):
        assert _bridge.resolve_cred_backend(None) == "file"


def test_resolve_cred_backend_cli_beats_env(monkeypatch):
    """CLI arg beats env var."""
    monkeypatch.setenv(_bridge._CRED_BACKEND_ENV, "keyring")
    assert _bridge.resolve_cred_backend("file") == "file"


def test_resolve_cred_backend_env_beats_config(monkeypatch):
    """Env var beats config file."""
    monkeypatch.setenv(_bridge._CRED_BACKEND_ENV, "keyring")
    with patch("mcpgen._bridge._load_client_config", return_value={"cred_backend": "auto"}):
        assert _bridge.resolve_cred_backend(None) == "keyring"


def test_resolve_cred_backend_config_file(monkeypatch, tmp_path):
    """Config file cred_backend key is used when no CLI arg or env var."""
    monkeypatch.delenv(_bridge._CRED_BACKEND_ENV, raising=False)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"cred_backend": "auto"}))
    with patch("mcpgen._bridge.DEFAULT_CONFIG_PATH", cfg):
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
        def get_password(self, s, u):
            raise RuntimeError("no keyring")

        def set_password(self, s, u, p):
            raise RuntimeError("no keyring")

    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("srv", credentials_path=creds, backend="keyring")

    with patch.dict("sys.modules", {"keyring": _BrokenKeyring()}), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(storage.set_tokens(OAuthToken(access_token="fb_token", token_type="bearer")))

    assert creds.exists(), "fallback must write to file"
    assert any("keyring" in str(w.message).lower() for w in caught), "must warn on fallback"
    assert stat.S_IMODE(os.stat(creds).st_mode) == 0o600, "fallback file must be 0600"


# ---------------------------------------------------------------------------
# migrate_creds — backend-to-backend credential migration
# ---------------------------------------------------------------------------


class _FakeKeyringMig(_FakeKeyring):
    """Extends _FakeKeyring with delete_password support for migration tests."""

    def __init__(self):
        super().__init__()
        self.delete_calls: list = []

    def delete_password(self, service, username):
        self.delete_calls.append((service, username))
        self._store.pop((service, username), None)


def _creds_data(*server_names: str) -> dict:
    """Build a minimal credentials dict for the given server names."""
    return {name: {"tokens": {"access_token": f"tok_{name}", "token_type": "bearer"}} for name in server_names}


def test_migrate_file_to_keyring_basic(tmp_path):
    """file → keyring: all entries copied; source file preserved (no --purge)."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme", "beta")))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        result = _bridge.migrate_creds("file", "keyring", credentials_path=creds)

    assert result["migrated"] == 2
    assert result["overwritten"] == 0
    assert result["purged"] is False
    assert result["set_default"] is False
    assert creds.exists(), "source file must be kept (no --purge)"
    assert fake_kr.set_calls, "keyring set_password must have been called"


def test_migrate_file_to_keyring_purge(tmp_path):
    """file → keyring --purge: source file removed after verified write."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme")))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        result = _bridge.migrate_creds("file", "keyring", credentials_path=creds, purge=True)

    assert result["purged"] is True
    assert not creds.exists(), "source file must be removed after purge"


def test_migrate_keyring_to_file(tmp_path):
    """keyring → file: round-trip produces correct JSON file."""
    creds = tmp_path / "credentials.json"
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        # Seed keyring
        _bridge._keyring_write_raw(_creds_data("svc"))
        result = _bridge.migrate_creds("keyring", "file", credentials_path=creds)

    assert result["migrated"] == 1
    assert creds.exists()
    data = json.loads(creds.read_text())
    assert "svc" in data


def test_migrate_collision_source_wins(tmp_path):
    """Collision: source entry overwrites target; target-only entries survive."""
    creds = tmp_path / "credentials.json"
    source_data = {"alpha": {"tokens": {"access_token": "src_tok", "token_type": "bearer"}}}
    target_data = {
        "alpha": {"tokens": {"access_token": "old_tok", "token_type": "bearer"}},
        "beta": {"tokens": {"access_token": "beta_tok", "token_type": "bearer"}},
    }
    creds.write_text(json.dumps(source_data))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        # Pre-seed target keyring
        _bridge._keyring_write_raw(target_data)
        result = _bridge.migrate_creds("file", "keyring", credentials_path=creds)
        merged = _bridge._keyring_read_raw()

    assert result["overwritten"] == 1
    assert merged["alpha"]["tokens"]["access_token"] == "src_tok", "source must win"
    assert "beta" in merged, "target-only entry must survive"


def test_migrate_empty_source_noop(tmp_path):
    """Empty source: no write, no purge, migrated == 0."""
    creds = tmp_path / "credentials.json"
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        result = _bridge.migrate_creds("keyring", "file", credentials_path=creds)

    assert result["migrated"] == 0
    assert not creds.exists(), "no target write for empty source"


def test_migrate_servers_subset(tmp_path):
    """--servers: only named entries migrate; other source entries untouched in target."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme", "beta", "gamma")))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        result = _bridge.migrate_creds("file", "keyring", servers=["acme", "beta"], credentials_path=creds)
        migrated_data = _bridge._keyring_read_raw()

    assert result["migrated"] == 2
    assert "acme" in migrated_data
    assert "beta" in migrated_data
    assert "gamma" not in migrated_data


def test_migrate_servers_subset_purge_partial(tmp_path):
    """--servers + --purge: only migrated keys removed from source; un-named entries remain."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme", "beta")))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge.migrate_creds("file", "keyring", servers=["acme"], credentials_path=creds, purge=True)

    remaining = json.loads(creds.read_text())
    assert "acme" not in remaining, "migrated key must be purged from source"
    assert "beta" in remaining, "un-named key must remain in source"


def test_migrate_servers_absent_name_raises(tmp_path):
    """--servers with absent name → ValueError before any write."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme")))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        with pytest.raises(ValueError, match="nosuchserver"):
            _bridge.migrate_creds("file", "keyring", servers=["acme", "nosuchserver"], credentials_path=creds)
        # Nothing written to keyring
        assert not fake_kr.set_calls


def test_migrate_same_backend_raises(tmp_path):
    """from == to → ValueError."""
    with pytest.raises(ValueError, match="nothing to migrate"):
        _bridge.migrate_creds("file", "file")


def test_migrate_set_default_creates_config(tmp_path):
    """--set-default: creates config.json with cred_backend when file is absent."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("svc")))
    cfg = tmp_path / "config.json"
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        result = _bridge.migrate_creds("file", "keyring", credentials_path=creds, set_default=True, config_path=cfg)

    assert result["set_default"] is True
    assert cfg.exists()
    assert json.loads(cfg.read_text())["cred_backend"] == "keyring"


def test_migrate_set_default_preserves_other_keys(tmp_path):
    """--set-default: existing config keys other than cred_backend are preserved."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("svc")))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"other_key": "other_val", "cred_backend": "file"}))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge.migrate_creds("file", "keyring", credentials_path=creds, set_default=True, config_path=cfg)

    data = json.loads(cfg.read_text())
    assert data["cred_backend"] == "keyring"
    assert data["other_key"] == "other_val", "other_key must be preserved"


def test_migrate_no_set_default_leaves_config_untouched(tmp_path):
    """Without --set-default, config.json is not touched."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("svc")))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"cred_backend": "file"}))
    fake_kr = _FakeKeyringMig()

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge.migrate_creds("file", "keyring", credentials_path=creds)

    assert json.loads(cfg.read_text())["cred_backend"] == "file", "config must be unchanged"


def test_migrate_keyring_read_failure_propagates(tmp_path):
    """Keyring read failure raises (strict — not silent fallback), source not purged."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("svc")))

    class _BrokenKeyring:
        def get_password(self, s, u):
            raise RuntimeError("no keyring")

        def set_password(self, s, u, p):
            raise RuntimeError("no keyring")

        def delete_password(self, s, u):
            raise RuntimeError("no keyring")

    with patch.dict("sys.modules", {"keyring": _BrokenKeyring()}):
        with pytest.raises(RuntimeError, match="no keyring"):
            _bridge.migrate_creds("keyring", "file", credentials_path=creds, purge=True)

    # Source (keyring) not purged — but verify source file was not inadvertently created
    # by checking that the purge path never ran (exception was raised before verify)


# ---------------------------------------------------------------------------
# list_creds / delete_cred
# ---------------------------------------------------------------------------


def _creds_data_with_expiry(past_name: str, future_name: str, noexp_name: str) -> dict:
    """Build a credentials dict with varied expiry states."""
    import time

    now = int(time.time())
    return {
        past_name: {"tokens": {"access_token": "tok_past", "token_type": "bearer", "expires_at": now - 3600}},
        future_name: {"tokens": {"access_token": "tok_future", "token_type": "bearer", "expires_at": now + 3600}},
        noexp_name: {"tokens": {"access_token": "tok_noexp", "token_type": "bearer"}},
    }


def test_list_creds_all_file(tmp_path):
    """list_creds returns all three entries with correct expired flag."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data_with_expiry("past", "future", "noexp")))
    os.chmod(creds, 0o600)

    rows = _bridge.list_creds(credentials_path=creds)
    by_name = {r["name"]: r for r in rows}

    assert set(by_name) == {"past", "future", "noexp"}
    assert by_name["past"]["expired"] is True
    assert by_name["future"]["expired"] is False
    assert by_name["noexp"]["expired"] is False
    assert by_name["noexp"]["expires_at"] is None


def test_list_creds_expired_only_file(tmp_path):
    """expired_only=True returns only the expired entry."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data_with_expiry("past", "future", "noexp")))
    os.chmod(creds, 0o600)

    rows = _bridge.list_creds(credentials_path=creds, expired_only=True)
    assert [r["name"] for r in rows] == ["past"]
    assert rows[0]["expired"] is True


def test_list_creds_empty_backend(tmp_path):
    """list_creds on an empty backend returns []."""
    creds = tmp_path / "credentials.json"
    rows = _bridge.list_creds(credentials_path=creds)
    assert rows == []


def test_list_creds_sorted(tmp_path):
    """list_creds returns entries sorted by server name."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("zeta", "alpha", "mu")))
    os.chmod(creds, 0o600)

    rows = _bridge.list_creds(credentials_path=creds)
    assert [r["name"] for r in rows] == ["alpha", "mu", "zeta"]


def test_list_creds_has_refresh_token(tmp_path):
    """has_refresh_token is True only when refresh_token key is present."""
    import time

    creds = tmp_path / "credentials.json"
    data = {
        "with_rt": {
            "tokens": {
                "access_token": "t",
                "token_type": "bearer",
                "refresh_token": "r",
                "expires_at": int(time.time()) + 7200,
            }
        },
        "without_rt": {"tokens": {"access_token": "t2", "token_type": "bearer"}},
    }
    creds.write_text(json.dumps(data))
    os.chmod(creds, 0o600)

    rows = _bridge.list_creds(credentials_path=creds)
    by_name = {r["name"]: r for r in rows}
    assert by_name["with_rt"]["has_refresh_token"] is True
    assert by_name["without_rt"]["has_refresh_token"] is False


def test_list_creds_keyring(tmp_path):
    """list_creds works with the keyring backend."""
    import time

    fake_kr = _FakeKeyringMig()
    now = int(time.time())
    kr_data = {
        "svcsA": {"tokens": {"access_token": "t", "token_type": "bearer", "expires_at": now - 100}},
    }
    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge._keyring_write_raw(kr_data)
        rows = _bridge.list_creds(backend="keyring", credentials_path=tmp_path / "c.json")

    assert len(rows) == 1
    assert rows[0]["name"] == "svcsA"
    assert rows[0]["expired"] is True


def test_delete_cred_existing_file(tmp_path):
    """delete_cred removes an existing entry and returns True."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme", "beta")))
    os.chmod(creds, 0o600)

    existed = _bridge.delete_cred("acme", credentials_path=creds)

    assert existed is True
    data = json.loads(creds.read_text())
    assert "acme" not in data
    assert "beta" in data


def test_delete_cred_absent_file(tmp_path):
    """delete_cred returns False when the entry does not exist."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("acme")))
    os.chmod(creds, 0o600)

    existed = _bridge.delete_cred("ghost", credentials_path=creds)

    assert existed is False
    assert json.loads(creds.read_text()) == _creds_data("acme")


def test_delete_cred_last_entry_clears_file(tmp_path):
    """delete_cred of the last entry unlinks the credentials file."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps(_creds_data("only")))
    os.chmod(creds, 0o600)

    existed = _bridge.delete_cred("only", credentials_path=creds)

    assert existed is True
    assert not creds.exists(), "file must be removed when no entries remain"


def test_delete_cred_keyring(tmp_path):
    """delete_cred works against the keyring backend."""
    fake_kr = _FakeKeyringMig()
    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge._keyring_write_raw(_creds_data("svcX", "svcY"))
        existed = _bridge.delete_cred("svcX", backend="keyring", credentials_path=tmp_path / "c.json")
        remaining = _bridge._keyring_read_raw()

    assert existed is True
    assert "svcX" not in remaining
    assert "svcY" in remaining


def test_delete_cred_last_keyring_clears(tmp_path):
    """delete_cred of last keyring entry calls _keyring_clear_raw (no residual key)."""
    fake_kr = _FakeKeyringMig()
    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge._keyring_write_raw(_creds_data("solo"))
        existed = _bridge.delete_cred("solo", backend="keyring", credentials_path=tmp_path / "c.json")
        remaining = _bridge._keyring_read_raw()

    assert existed is True
    assert remaining == {}


def test_keyring_clear_raw_propagates_non_notfound(tmp_path):
    """_keyring_clear_raw propagates errors that are not PasswordDeleteError.

    A locked keychain or access-denied failure must not be silently eaten —
    callers reporting deletion success when the entry still exists breaks the
    security contract.
    """

    class _LockedKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        def get_password(self, s, u):
            return None

        def set_password(self, s, u, p):
            pass

        def delete_password(self, s, u):
            raise RuntimeError("keychain locked")

    with patch.dict("sys.modules", {"keyring": _LockedKeyring()}):
        with pytest.raises(RuntimeError, match="keychain locked"):
            _bridge._keyring_clear_raw()


def test_keyring_clear_raw_silent_on_not_found(tmp_path):
    """_keyring_clear_raw is a no-op (no raise) when the entry is absent."""

    class _EmptyKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass

        def get_password(self, s, u):
            return None

        def set_password(self, s, u, p):
            pass

        def delete_password(self, s, u):
            raise self.errors.PasswordDeleteError("no such entry")

    with patch.dict("sys.modules", {"keyring": _EmptyKeyring()}):
        _bridge._keyring_clear_raw()  # must not raise


# ---------------------------------------------------------------------------
# login() — credential preservation on OAuth failure
# ---------------------------------------------------------------------------


def test_login_restores_credential_on_oauth_failure(tmp_path):
    """login() restores the prior credential when the OAuth flow fails.

    If the network is down, the user cancels, or the server rejects
    dynamic registration, the original access/refresh token must survive —
    the user must not be locked out of a previously-working server.
    """
    creds = tmp_path / "credentials.json"
    original_entry = {"tokens": {"access_token": "orig_tok", "token_type": "bearer"}}
    creds.write_text(json.dumps({"acme": original_entry}))
    os.chmod(creds, 0o600)

    async def fake_callback_server():
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(("code", "state"))
        return 9999, fut

    @asynccontextmanager
    async def fake_http_fail(*args, **kwargs):
        raise RuntimeError("network error")
        yield  # makes this an async generator; unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", fake_callback_server),
            patch("mcpgen._bridge._open_http", fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())
    data = json.loads(creds.read_text())
    assert "acme" in data, "prior credential must be restored after failed login"
    assert data["acme"] == original_entry


def test_login_no_prior_credential_does_not_create_on_failure(tmp_path):
    """login() failure when no prior credential existed leaves no partial entry."""
    creds = tmp_path / "credentials.json"

    async def fake_callback_server():
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(("code", "state"))
        return 9999, fut

    @asynccontextmanager
    async def fake_http_fail(*args, **kwargs):
        raise RuntimeError("network error")
        yield

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", fake_callback_server),
            patch("mcpgen._bridge._open_http", fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("newserver", creds_path=creds, url="https://new.example.com/mcp")

    asyncio.run(run())
    # Either no file, or file exists but "newserver" is absent.
    if creds.exists():
        data = json.loads(creds.read_text())
        assert "newserver" not in data


# ---------------------------------------------------------------------------
# login() — failures *after* the token exchange
# ---------------------------------------------------------------------------


def _provider_that_completes_token_exchange(server_name, fresh_tokens, token_endpoint=None):
    """Stand-in for OAuthClientProvider that writes a token as it is constructed.

    The real SDK saves the exchanged token from inside the auth handshake, i.e.
    before `ClientSession.initialize()` returns. So by the time anything later in
    login() blows up, a perfectly good credential is already on disk — that is the
    state these tests reproduce. Metadata discovery happens earlier still, so the
    provider can already resolve the token endpoint by then.

    `_get_token_endpoint` mirrors the SDK's own: the discovered endpoint when there
    is one, else `<origin>/token`. Pass `token_endpoint=None` to model a server that
    publishes no discovery document.
    """

    def fake_provider(**kwargs):
        storage = kwargs["storage"]
        data = storage._load()
        data.setdefault(server_name, {})["tokens"] = fresh_tokens
        storage._save(data)
        metadata = SimpleNamespace(token_endpoint=token_endpoint) if token_endpoint else None
        origin = urlparse(kwargs["server_url"])
        return SimpleNamespace(
            context=SimpleNamespace(oauth_metadata=metadata),
            _get_token_endpoint=lambda: (
                str(token_endpoint) if token_endpoint else f"{origin.scheme}://{origin.netloc}/token"
            ),
        )

    return fake_provider


def _run_login_failing_after_exchange(creds, exc, fresh_tokens, token_endpoint=None):
    """Drive login() to the point where the token is saved, then fail with *exc*."""

    @asynccontextmanager
    async def fake_http(*args, **kwargs):
        raise exc
        yield  # makes this an async generator; unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", fake_http),
            patch(
                "mcpgen._bridge.OAuthClientProvider",
                _provider_that_completes_token_exchange("acme", fresh_tokens, token_endpoint),
            ),
        ):
            await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    return run


def test_login_keeps_token_when_server_fails_after_token_exchange(tmp_path):
    """A freshly-exchanged token must survive a post-authentication failure.

    The stash/restore exists for a login that never produced a credential. Rolling
    it back over a token the authorization server just issued turns a transient
    origin outage (e.g. a 502 on `initialize`) into an endless browser re-prompt:
    every later run finds the stale entry, reauthenticates, and discards the
    result again.
    """
    creds = tmp_path / "credentials.json"
    stale_entry = {
        "tokens": {"access_token": "stale_tok", "token_type": "bearer", "expires_at": 1},
        "client_info": {"client_id": "old_id"},
    }
    creds.write_text(json.dumps({"acme": stale_entry}))
    os.chmod(creds, 0o600)

    fresh = {"access_token": "fresh_tok", "token_type": "bearer", "expires_at": 9999999999}
    run = _run_login_failing_after_exchange(creds, RuntimeError("502 Bad Gateway"), fresh)

    with pytest.raises(_bridge.PostLoginCheckFailed):
        asyncio.run(run())

    assert json.loads(creds.read_text())["acme"]["tokens"] == fresh


def test_login_reports_server_unavailable_distinctly(tmp_path):
    """A post-auth failure raises PostLoginCheckFailed, not the raw transport error.

    Callers (batch runs especially) need to tell "the server is down, the token is
    fine" apart from "you must log in again" — retrying login for the former is
    pure interactive churn.
    """
    creds = tmp_path / "credentials.json"
    original = RuntimeError("Server error '502 Bad Gateway'")
    run = _run_login_failing_after_exchange(creds, original, {"access_token": "fresh_tok"})

    with pytest.raises(_bridge.PostLoginCheckFailed) as excinfo:
        asyncio.run(run())

    assert excinfo.value.__cause__ is original
    assert "502 Bad Gateway" in str(excinfo.value)


def test_login_classifies_wrapped_transport_failure_as_server_unavailable(tmp_path):
    """The real failure arrives as an ExceptionGroup out of the anyio task group."""
    creds = tmp_path / "credentials.json"
    group = ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("502 Bad Gateway")])
    run = _run_login_failing_after_exchange(creds, group, {"access_token": "fresh_tok"})

    with pytest.raises(_bridge.PostLoginCheckFailed) as excinfo:
        asyncio.run(run())

    # The wrapper's own str() is "unhandled errors in a TaskGroup (1 sub-exception)":
    # useless to whoever has to tell a 502 from a DNS failure. The leaf must survive.
    assert "502 Bad Gateway" in str(excinfo.value)
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "fresh_tok"


def test_describe_flattens_nested_groups_and_keeps_bare_exception_types():
    """_describe() is what stands between the operator and a useless error line."""
    nested = ExceptionGroup(
        "outer",
        [ExceptionGroup("inner", [RuntimeError("502 Bad Gateway")]), ValueError("bad state")],
    )
    described = _bridge._describe(nested)
    assert "502 Bad Gateway" in described
    assert "bad state" in described
    # An exception with an empty message still has to name itself.
    assert _bridge._describe(RuntimeError()) == "RuntimeError"


def test_login_does_not_convert_keyboard_interrupt(tmp_path):
    """Ctrl-C stays Ctrl-C, even once a token has been saved."""
    creds = tmp_path / "credentials.json"
    run = _run_login_failing_after_exchange(creds, KeyboardInterrupt(), {"access_token": "fresh_tok"})

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run())

    # Still no rollback: the token is real regardless of how the run ended.
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "fresh_tok"


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(1), asyncio.CancelledError()])
def test_login_does_not_convert_interrupt_wrapped_by_a_task_group(tmp_path, interrupt):
    """A Ctrl-C inside the session arrives wrapped — it must still read as an interrupt.

    anyio task groups wrap even a single exception (that is how the transport
    error surfaces too), so a flat isinstance check on the outermost exception
    would relabel a user's Ctrl-C as a server fault and exit 1.
    """
    creds = tmp_path / "credentials.json"
    wrapped = BaseExceptionGroup("unhandled errors in a TaskGroup", [interrupt])
    run = _run_login_failing_after_exchange(creds, wrapped, {"access_token": "fresh_tok"})

    with pytest.raises(BaseExceptionGroup) as excinfo:
        asyncio.run(run())

    assert not isinstance(excinfo.value, _bridge.PostLoginCheckFailed)
    assert excinfo.value.exceptions[0] is interrupt


def test_login_persists_token_endpoint_when_server_fails_after_token_exchange(tmp_path):
    """The kept token needs its token endpoint kept alongside it.

    `_pre_flight_refresh` treats a missing `token_endpoint` as "must log in
    again", so a token saved without one silently expires into the very browser
    re-prompt this path exists to prevent. The normal persistence runs after
    `initialize()`, which is exactly what failed here.
    """
    creds = tmp_path / "credentials.json"
    run = _run_login_failing_after_exchange(
        creds,
        RuntimeError("502 Bad Gateway"),
        {"access_token": "fresh_tok", "refresh_token": "fresh_refresh", "expires_at": 1},
        token_endpoint="https://auth.example.com/token",
    )

    with pytest.raises(_bridge.PostLoginCheckFailed):
        asyncio.run(run())

    assert json.loads(creds.read_text())["acme"]["token_endpoint"] == "https://auth.example.com/token"


def test_sdk_provider_resolves_token_endpoint(tmp_path):
    """Pin the SDK member `_persist_token_endpoint` calls.

    Every test around it drives a SimpleNamespace stand-in, so nothing else here
    would notice `mcp` renaming `context` or `_get_token_endpoint` — the endpoint
    would silently stop being cached and every credential would expire into a
    browser prompt with the suite still green. `mcp` is pinned to a range, not a
    version, and `_get_token_endpoint` is private, so this is a live risk.
    Constructing the provider and resolving the URL are both pure; no network.
    """
    from mcp.client.auth import OAuthClientProvider

    async def _unused(*args, **kwargs):  # pragma: no cover — handlers are never invoked
        raise AssertionError("handler must not run")

    provider = OAuthClientProvider(
        server_url="https://acme.example.com/mcp",
        client_metadata=_bridge._client_metadata("acme", "http://localhost:9999/callback"),
        storage=_bridge.FileTokenStorage("acme", tmp_path / "credentials.json"),
        redirect_handler=_unused,
        callback_handler=_unused,
    )

    # Undiscovered — the state a server publishing no metadata document leaves it in.
    assert provider.context.oauth_metadata is None
    # …and the SDK still resolves a usable endpoint, which is the whole reason we ask
    # it instead of reading oauth_metadata ourselves.
    assert provider._get_token_endpoint() == "https://acme.example.com/token"


def test_login_persists_the_endpoint_that_issued_the_token(tmp_path):
    """With no discovery document, cache the SDK's fallback — not the old login's URL.

    Reaching this branch means the exchange succeeded, and with `oauth_metadata`
    unset it succeeded against `<origin>/token`. That URL is therefore proven to
    work; the endpoint a previous login discovered is not, and inheriting it would
    point the next refresh somewhere the token was never issued.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {
                "acme": {
                    "tokens": {"access_token": "stale_tok", "expires_at": 1},
                    "token_endpoint": "https://auth.example.com/oauth/token",
                }
            }
        )
    )
    os.chmod(creds, 0o600)

    run = _run_login_failing_after_exchange(
        creds,
        RuntimeError("502 Bad Gateway"),
        {"access_token": "fresh_tok", "expires_at": 9999999999},
        token_endpoint=None,  # discovery produced nothing
    )

    with pytest.raises(_bridge.PostLoginCheckFailed):
        asyncio.run(run())

    entry = json.loads(creds.read_text())["acme"]
    assert entry["tokens"]["access_token"] == "fresh_tok", "the fresh token still wins"
    assert entry["token_endpoint"] == "https://acme.example.com/token"


def test_login_discovered_token_endpoint_beats_the_stashed_one(tmp_path):
    """A rediscovered endpoint must not be shadowed by the previous login's copy."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "stale"}, "token_endpoint": "https://old/token"}}))
    os.chmod(creds, 0o600)

    run = _run_login_failing_after_exchange(
        creds,
        RuntimeError("502 Bad Gateway"),
        {"access_token": "fresh_tok"},
        token_endpoint="https://new/token",
    )

    with pytest.raises(_bridge.PostLoginCheckFailed):
        asyncio.run(run())

    assert json.loads(creds.read_text())["acme"]["token_endpoint"] == "https://new/token"


def test_login_post_auth_message_does_not_claim_the_credential_works(tmp_path):
    """A post-login 401 reaches this path too — the message must not vouch for the token.

    Token issuance says nothing about whether the resource server accepts it. The one
    thing that holds for every cause is that logging in again will not change it.
    """
    creds = tmp_path / "credentials.json"
    run = _run_login_failing_after_exchange(
        creds,
        ExceptionGroup("tg", [RuntimeError("Client error '401 Unauthorized'")]),
        {"access_token": "fresh_tok"},
        token_endpoint="https://auth.example.com/token",
    )

    with pytest.raises(_bridge.PostLoginCheckFailed) as excinfo:
        asyncio.run(run())

    message = str(excinfo.value)
    assert "401 Unauthorized" in message, "the cause has to reach the operator"
    assert "credential is valid" not in message
    assert "Logging in again will not change this" in message


def test_login_kept_token_is_refreshable_without_a_new_login(tmp_path):
    """End to end: after the 502, the next run refreshes silently instead of re-prompting."""
    creds = tmp_path / "credentials.json"
    run = _run_login_failing_after_exchange(
        creds,
        RuntimeError("502 Bad Gateway"),
        {"access_token": "fresh_tok", "refresh_token": "fresh_refresh", "expires_at": 1},
        token_endpoint="https://auth.example.com/token",
    )

    with pytest.raises(_bridge.PostLoginCheckFailed):
        asyncio.run(run())

    # login() cleared client_info and the fake provider never registered one, so
    # supply what a real registration would have left behind.
    data = json.loads(creds.read_text())
    data["acme"]["client_info"] = {"client_id": "new_id"}
    creds.write_text(json.dumps(data))

    posted = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "renewed_tok", "token_type": "bearer"}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data):
            posted["url"] = url
            return _FakeResponse()

    async def refresh():
        storage = _bridge.FileTokenStorage("acme", creds)
        with patch("mcpgen._bridge.httpx.AsyncClient", _FakeClient):
            await _bridge._pre_flight_refresh("acme", storage)

    asyncio.run(refresh())
    assert posted["url"] == "https://auth.example.com/token"
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "renewed_tok"


def test_login_says_so_when_the_kept_token_cannot_be_refreshed(tmp_path):
    """A token kept but left unrenewable must say so on the line the operator reads.

    Failing to cache the token endpoint is the one condition that quietly reinstates
    the re-prompt this whole branch exists to prevent: the token survives the outage
    and then expires with no way to renew it. It used to go through warnings.warn —
    shown once per location, and gone entirely under PYTHONWARNINGS=ignore.
    """
    creds = tmp_path / "credentials.json"
    run = _run_login_failing_after_exchange(
        creds,
        RuntimeError("502 Bad Gateway"),
        {"access_token": "fresh_tok"},
        token_endpoint="https://auth.example.com/token",
    )

    def explode(*args, **kwargs):
        raise OSError("disk full")

    with (
        patch("mcpgen._bridge._persist_token_endpoint", explode),
        pytest.raises(_bridge.PostLoginCheckFailed) as excinfo,
    ):
        asyncio.run(run())

    message = str(excinfo.value)
    assert "disk full" in message, "the operator has to know why it cannot be refreshed"
    assert "502 Bad Gateway" in message, "and the original failure must still be there"
    # The token is still kept — an unrenewable token beats no token at all.
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "fresh_tok"


def test_login_does_not_claim_unrenewable_when_an_endpoint_is_already_cached(tmp_path):
    """Only claim the token cannot be refreshed when no endpoint is actually on disk.

    initialize() persists the endpoint too, so a list_tools() failure reaches the
    handler with one already cached. Saying "cannot be refreshed and the next run
    will prompt" there is simply false, and false remediation advice is how an
    operator ends up back at the browser for no reason.
    """
    creds = tmp_path / "credentials.json"

    # login() pops the whole entry before the flow (_bridge.py:1492), so a previous
    # login's endpoint is never on disk here — the only way one is cached is the
    # persist that follows a successful initialize(). Write it from the provider to
    # reproduce that state: initialize() succeeded, list_tools() is what failed.
    def provider_that_also_caches_the_endpoint(**kwargs):
        storage = kwargs["storage"]
        data = storage._load()
        entry = data.setdefault("acme", {})
        entry["tokens"] = {"access_token": "fresh_tok"}
        entry["token_endpoint"] = "https://auth.example.com/token"
        storage._save(data)
        return SimpleNamespace(
            context=SimpleNamespace(oauth_metadata=None),
            _get_token_endpoint=lambda: "https://auth.example.com/token",
        )

    @asynccontextmanager
    async def fake_http(*args, **kwargs):
        raise RuntimeError("502 Bad Gateway")
        yield  # makes this an async generator; unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider_that_also_caches_the_endpoint),
        ):
            await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    def explode(*args, **kwargs):
        raise OSError("disk full")

    with (
        patch("mcpgen._bridge._persist_token_endpoint", explode),
        pytest.raises(_bridge.PostLoginCheckFailed) as excinfo,
    ):
        asyncio.run(run())

    message = str(excinfo.value)
    assert "disk full" in message, "the operator still has to know the save failed"
    assert "will prompt for a new login" not in message, "there is a cached endpoint to refresh with"
    assert "has not moved" in message


def test_describe_caps_a_runaway_exception_message():
    """One CLI line, so a leaf carrying an HTML error page has to be truncated."""
    described = _bridge._describe(RuntimeError("x" * 5000))

    assert len(described) < 400
    assert described.startswith("RuntimeError: xxx")
    assert described.endswith("…")


def test_sdk_saves_tokens_before_initialize_returns(tmp_path):
    """Pin the SDK ordering the whole fix rests on, against the real SDK.

    login() keeps a token only because OAuthClientProvider persists it from inside
    the httpx auth handshake — i.e. before `ClientSession.initialize()` returns. If
    a future mcp 1.x moved that write after the retry, the "no token produced"
    branch would take over, the stash would be restored, and the original bug would
    come back with every test still green: the fake in these tests writes the token
    itself, so it can never catch that drift.

    Driving `async_auth_flow` directly is the cheapest honest check. A source grep
    would pass on a refactor that breaks the ordering and fail on a rename that
    does not.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    saved: list[OAuthToken] = []

    class _Storage:
        async def get_tokens(self):
            return None

        async def set_tokens(self, tokens):
            saved.append(tokens)

        async def get_client_info(self):
            return OAuthClientInformationFull(
                client_id="cid",
                redirect_uris=[AnyUrl("http://localhost:9999/callback")],
                token_endpoint_auth_method="none",
            )

        async def set_client_info(self, info):
            return None

    issued_state = {}

    async def redirect_handler(url: str) -> None:
        # The SDK checks the returned state with compare_digest, so echo its own.
        issued_state["value"] = parse_qs(urlparse(url).query)["state"][0]

    async def callback_handler():
        # KeyError here means the SDK stopped putting `state` in the authorization
        # URL — again a change of flow shape, not of the write ordering.
        return "auth_code", issued_state["value"]

    provider = OAuthClientProvider(
        server_url="https://acme.example.com/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="mcpgen",
            redirect_uris=[AnyUrl("http://localhost:9999/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=_Storage(),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    token_body = json.dumps(
        {"access_token": "issued_tok", "token_type": "Bearer", "expires_in": 3600}
    ).encode()

    # Recorded inside the flow, asserted outside it: raising through `asend` unwinds
    # the SDK's own lock and surfaces as an unrelated "task is not holding this lock".
    saved_before_retry = {}

    async def drive():
        flow = provider.async_auth_flow(httpx.Request("POST", "https://acme.example.com/mcp"))
        request = await flow.__anext__()
        # Feed the 401 that starts the OAuth dance, then answer whatever the SDK asks
        # for until it stops. The token response is the only one that matters here.
        response = httpx.Response(401, request=request)
        # Bounded, not `while True`: the current SDK yields a handful of times (two
        # finite discovery lists, an optional registration, the token POST, the
        # retry), but an SDK that loops would hang the suite instead of failing it.
        # Falling out of the loop leaves `saved_before_retry` unset, which fails.
        for _ in range(20):
            try:
                request = await flow.asend(response)
            except StopAsyncIteration:
                return
            url = str(request.url)
            # Routing keys on the SDK's current URL shapes. If those move, requests
            # fall to the `else` below and surface as a pydantic validation error:
            # that means the flow's shape changed, not that the ordering broke.
            if url.endswith("/token"):
                response = httpx.Response(200, content=token_body, request=request)
            elif "well-known" in url:
                response = httpx.Response(404, request=request)
            else:
                response = httpx.Response(200, content=b"{}", request=request)
            # The retried request is the original one: the handshake is over by the
            # time the SDK yields it, so the token has to be on disk already.
            if url.startswith("https://acme.example.com/mcp"):
                saved_before_retry["value"] = bool(saved)
                return

    asyncio.run(drive())
    assert saved_before_retry.get("value") is True, (
        "the SDK must persist the token before it yields the retried request — "
        "login() keeps a post-failure token only because of this ordering"
    )
    assert saved[0].access_token == "issued_tok"


def test_ensure_login_all_aborts_the_batch_on_post_login_failure(tmp_path):
    """The contract USAGE.md sells to batch callers: stop, do not walk the rest.

    ensure_login() catches ReauthenticationRequired and only that, so
    PostLoginCheckFailed propagates and the loop stops. That is the entire point of
    the new type — a batch that keeps going prompts once per remaining server, which
    is the reported incident.
    """
    creds = tmp_path / "credentials.json"
    attempted = []

    async def fake_login(server_name, *args, **kwargs):
        attempted.append(server_name)
        raise _bridge.PostLoginCheckFailed(f"post-login check failed ({server_name})")

    async def run():
        with patch("mcpgen._bridge.login", fake_login):
            await _bridge.ensure_login_all(["first", "second"], creds_path=creds)

    with pytest.raises(_bridge.PostLoginCheckFailed):
        asyncio.run(run())

    assert attempted == ["first"], "the second server must never reach the browser"


# ---------------------------------------------------------------------------
# Public-client registration (token_endpoint_auth_method="none")
# ---------------------------------------------------------------------------


def _fake_callback_server_factory():
    async def fake_callback_server():
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(("code", "state"))
        return 9999, fut

    return fake_callback_server


@asynccontextmanager
async def _fake_http_fail(*args, **kwargs):
    raise RuntimeError("network error")
    yield  # makes this an async generator; unreachable


def test_registration_request_body_says_none():
    """The public-client declaration must survive serialization into the DCR body.

    The tests below stop at mcpgen's boundary: they assert on the OAuthClientMetadata
    we hand the SDK. This one goes one layer further and pins the actual wire contract,
    because the SDK is pinned only to `mcp<2` — a future release that changed the alias,
    the exclude_none behaviour, or the field itself would leave those tests green while
    mcpgen silently regressed to the double-auth bug. create_client_registration_request
    is pure, so this costs no network.
    """
    from mcp.client.auth.utils import create_client_registration_request

    metadata = _bridge._client_metadata("acme", "http://localhost:9999/callback")
    request = create_client_registration_request(None, metadata, "https://acme.example.com/")

    body = json.loads(request.content)
    assert body["token_endpoint_auth_method"] == "none"


def test_login_registers_public_client(tmp_path):
    """login() must ask to be registered as a public client.

    Omitting token_endpoint_auth_method makes the AS default the client to
    client_secret_basic (RFC 7591 §2). The SDK then sends both an
    Authorization: Basic header and client_id in the form body, which strict
    servers reject with 400 "Client must not use multiple authentication
    methods" (RFC 6749 §2.3). PKCE already secures the flow.

    This pins the metadata mcpgen *requests*; test_registration_request_body_says_none
    pins that it reaches the wire.
    """
    creds = tmp_path / "credentials.json"
    provider_cls = MagicMock()

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", provider_cls),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())
    metadata = provider_cls.call_args.kwargs["client_metadata"]
    assert metadata.token_endpoint_auth_method == "none"


def test_http_session_registers_public_client(tmp_path):
    """_http_session() must request a public client too — the other OAuth entry point."""
    creds = tmp_path / "credentials.json"
    provider_cls = MagicMock()

    seen_backend: dict = {}

    class _TmpStorage(_bridge.FileTokenStorage):
        """Real FileTokenStorage pinned to tmp_path.

        _http_session() takes no creds_path, so it uses FileTokenStorage's
        DEFAULT_CREDS_PATH default — bound at def time, hence not redirectable
        by patching the module attribute. Only the path is overridden; the backend
        is passed through so this cannot mask a regression in cred_backend routing.
        """

        def __init__(self, server_name, credentials_path=None, backend="file"):
            seen_backend["backend"] = backend
            super().__init__(server_name, creds, backend=backend)

    async def run():
        with (
            patch("mcpgen._bridge.FileTokenStorage", _TmpStorage),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", provider_cls),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                async with _bridge._http_session("acme", "https://acme.example.com/mcp"):
                    pass

    asyncio.run(run())
    metadata = provider_cls.call_args.kwargs["client_metadata"]
    assert metadata.token_endpoint_auth_method == "none"
    assert seen_backend["backend"] == "file", "_http_session must route cred_backend into storage"


def test_login_hands_provider_no_stale_client_info(tmp_path):
    """login() must never let the SDK reuse a stored client registration.

    The SDK skips registration when client_info is already in storage
    (`if not self.context.client_info`). login() stashes and clears the whole
    entry first, so a credential registered before the public-client fix cannot
    poison a fresh login — it is always re-registered. This pins that invariant;
    without it, existing users would keep failing after the fix.
    """
    creds = tmp_path / "credentials.json"
    stale_entry = {
        "tokens": {"access_token": "orig_tok", "token_type": "bearer"},
        "client_info": {
            "client_id": "old_id",
            "client_secret": "shh",
            "token_endpoint_auth_method": "client_secret_basic",
        },
    }
    creds.write_text(json.dumps({"acme": stale_entry}))
    os.chmod(creds, 0o600)

    seen: dict = {}

    def fake_provider(**kwargs):
        # Read through the real storage seam at the moment the SDK would.
        seen["client_info"] = kwargs["storage"]._load().get("acme", {}).get("client_info")
        return MagicMock()

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", fake_provider),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())
    assert seen["client_info"] is None, "stale client registration must not survive into a login"
    # …and the failed login must still restore it, so the user is not locked out.
    assert json.loads(creds.read_text())["acme"] == stale_entry


def test_login_explains_public_client_rejection(tmp_path):
    """An AS that refuses public clients must produce a legible error, not a bare 400.

    Registering as a public client is unconditional, so an AS that requires a
    client_secret rejects us with invalid_client_metadata (RFC 7591 §2 — it must
    reject rather than downgrade). No such server is known, so instead of carrying a
    speculative override flag we name the cause if one ever turns up.
    """
    from mcp.client.auth import OAuthRegistrationError

    creds = tmp_path / "credentials.json"

    @asynccontextmanager
    async def _fail_registration(*args, **kwargs):
        raise OAuthRegistrationError(
            'Registration failed: 400 {"error":"invalid_client_metadata",'
            '"error_description":"client_secret is required"}'
        )
        yield  # unreachable; makes this an async generator

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", _fail_registration),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(OAuthRegistrationError) as exc:
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")
        return str(exc.value)

    message = asyncio.run(run())
    assert "invalid_client_metadata" in message, "the server's own error must be preserved"
    assert "token_endpoint_auth_method=none" in message, "the likely cause must be named"


def test_login_does_not_explain_unrelated_registration_error(tmp_path):
    """Only invalid_client_metadata gets the public-client annotation — no over-claiming."""
    from mcp.client.auth import OAuthRegistrationError

    creds = tmp_path / "credentials.json"

    @asynccontextmanager
    async def _fail_registration(*args, **kwargs):
        raise OAuthRegistrationError("Registration failed: 503 upstream unavailable")
        yield  # unreachable; makes this an async generator

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", _fail_registration),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(OAuthRegistrationError) as exc:
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")
        return str(exc.value)

    message = asyncio.run(run())
    assert "503 upstream unavailable" in message
    assert "token_endpoint_auth_method" not in message


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


# ---------------------------------------------------------------------------
# parse() / call() — non-text content blocks (image/resource/resource_link)
# must not collapse to an empty string (#4)
# ---------------------------------------------------------------------------


def test_parse_image_block_returns_marker_not_empty_string():
    """An image block (no .text) must not fall through to `""`."""
    item = {"type": "image", "mimeType": "image/png", "has_data": True}
    result = _bridge.parse([item])
    assert result != ""
    assert result["type"] == "image"
    assert result["mimeType"] == "image/png"


def test_parse_resource_block_returns_marker_not_empty_string():
    """A resource block carrying only a blob (no text) must not collapse to `""`."""
    item = {"type": "resource", "mimeType": "application/gzip", "has_text": False, "has_blob": True}
    result = _bridge.parse([item])
    assert result != ""
    assert result["type"] == "resource"
    assert result["has_blob"] is True


def test_parse_resource_link_block_returns_marker():
    item = {"type": "resource_link", "uri": "file:///tmp/x.txt", "name": "x.txt"}
    result = _bridge.parse([item])
    assert result["type"] == "resource_link"
    assert result["uri"] == "file:///tmp/x.txt"


def test_parse_text_block_unaffected_by_marker_handling():
    """Regression: ordinary text blocks still JSON-parse as before."""
    result = _bridge.parse([{"type": "text", "text": '{"a": 1}'}])
    assert result == {"a": 1}


class _FakeImageItem:
    def __init__(self, mimeType, data):
        self.type = "image"
        self.mimeType = mimeType
        self.data = data


class _FakeResource:
    def __init__(self, mimeType=None, text=None, blob=None):
        self.mimeType = mimeType
        self.text = text
        self.blob = blob


class _FakeResourceItem:
    def __init__(self, resource):
        self.type = "resource"
        self.resource = resource


class _FakeResourceLinkItem:
    def __init__(self, uri, name):
        self.type = "resource_link"
        self.uri = uri
        self.name = name


def _run_call_with_content(item):
    mock_session = MagicMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[item]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield mock_session

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller()
            return await caller.call("s", "t", {})

    return asyncio.run(run())


def test_mcp_bridge_caller_image_content_not_collapsed_to_empty_string():
    result = _run_call_with_content(_FakeImageItem(mimeType="image/png", data="abc123=="))
    assert result != ""
    assert result["type"] == "image"
    assert result["mimeType"] == "image/png"
    assert result["has_data"] is True


def test_mcp_bridge_caller_resource_content_not_collapsed_to_empty_string():
    """gzip-file-as-resource with no `.text` alongside the blob — must not vanish to `""`."""
    result = _run_call_with_content(_FakeResourceItem(_FakeResource(mimeType="application/gzip", blob="H4sIAAAA")))
    assert result != ""
    assert result["type"] == "resource"
    assert result["mimeType"] == "application/gzip"
    assert result["has_blob"] is True
    assert result["has_text"] is False


def test_mcp_bridge_caller_resource_link_content_captured():
    result = _run_call_with_content(_FakeResourceLinkItem(uri="file:///tmp/x.txt", name="x.txt"))
    assert result["type"] == "resource_link"
    assert result["uri"] == "file:///tmp/x.txt"
    assert result["name"] == "x.txt"


def test_mcp_bridge_caller_resource_link_uri_is_json_serializable():
    """Real MCP SDK ResourceLink.uri is a pydantic AnyUrl, not a str — the summary
    dict must convert it, or json.dumps (called by _cmd_call/_probe) crashes."""
    from pydantic import AnyUrl

    result = _run_call_with_content(_FakeResourceLinkItem(uri=AnyUrl("file:///tmp/x.txt"), name="x.txt"))
    assert isinstance(result["uri"], str)
    json.dumps(result)  # must not raise TypeError


def test_mcp_bridge_caller_resource_link_missing_uri_stays_none():
    result = _run_call_with_content(_FakeResourceLinkItem(uri=None, name="x.txt"))
    assert result["uri"] is None
    json.dumps(result)  # must not raise TypeError


# ---------------------------------------------------------------------------
# Headless login — callback parsing, detection, login()/ensure_login_all(), CLI
# ---------------------------------------------------------------------------


def test_parse_callback_query_returns_code_and_state():
    assert _bridge._parse_callback_query("code=abc&state=xyz") == ("abc", "xyz")


def test_parse_callback_query_raises_on_error_with_description():
    with pytest.raises(ValueError) as exc:
        _bridge._parse_callback_query("error=access_denied&error_description=User+said+no")
    assert "access_denied" in str(exc.value)
    assert "User said no" in str(exc.value)


def test_parse_callback_query_error_without_description():
    with pytest.raises(ValueError, match=r"\(no description\)"):
        _bridge._parse_callback_query("error=server_error")


def test_parse_callback_query_missing_code_returns_nones():
    """A bare redirect (no code, no error) is not an error — the caller decides."""
    assert _bridge._parse_callback_query("") == (None, None)


def test_is_headless_env_override_forces_headless(monkeypatch):
    monkeypatch.setenv("MCPGEN_HEADLESS", "1")
    with patch("sys.platform", "darwin"):
        assert _bridge._is_headless() is True


def test_is_headless_env_override_forces_interactive(monkeypatch):
    monkeypatch.setenv("MCPGEN_HEADLESS", "0")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with patch("sys.platform", "linux"):
        assert _bridge._is_headless() is False


def test_is_headless_false_on_desktop_platforms(monkeypatch):
    monkeypatch.delenv("MCPGEN_HEADLESS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    for platform in ("darwin", "win32"):
        with patch("sys.platform", platform):
            assert _bridge._is_headless() is False


def test_is_headless_true_on_linux_without_display(monkeypatch):
    monkeypatch.delenv("MCPGEN_HEADLESS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with patch("sys.platform", "linux"):
        assert _bridge._is_headless() is True


def test_is_headless_false_on_linux_with_x11_display(monkeypatch):
    monkeypatch.delenv("MCPGEN_HEADLESS", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with patch("sys.platform", "linux"):
        assert _bridge._is_headless() is False


def test_is_headless_false_on_linux_with_wayland_display(monkeypatch):
    monkeypatch.delenv("MCPGEN_HEADLESS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    with patch("sys.platform", "linux"):
        assert _bridge._is_headless() is False


def test_login_headless_skips_callback_server(tmp_path):
    """headless=True must not bind a socket, and must register the port-less URI.

    The redirect URI is never fetched in this mode — the user pastes it back —
    so it stays port-less, which keeps the registered value stable across runs.
    """
    creds = tmp_path / "credentials.json"
    provider_cls = MagicMock()
    server = MagicMock()

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", server),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", provider_cls),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=True)

    asyncio.run(run())
    server.assert_not_called()
    metadata = provider_cls.call_args.kwargs["client_metadata"]
    assert [str(u) for u in metadata.redirect_uris] == ["http://localhost/callback"]
    assert metadata.token_endpoint_auth_method == "none"


def _capture_provider_and_invoke_callback():
    """Provider factory + _open_http fake that drives the captured callback_handler.

    _fake_http_fail short-circuits before the handler ever runs; this pair lets a
    test exercise the headless stdin path end to end inside login().
    """
    captured: dict = {}

    def provider(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    @asynccontextmanager
    async def fake_http(*args, **kwargs):
        await captured["callback_handler"]()
        yield  # unreachable

    return provider, fake_http


def test_login_headless_empty_stdin_aborts_and_restores_credential(tmp_path):
    """An empty paste aborts the login and leaves the prior credential intact."""
    creds = tmp_path / "credentials.json"
    original_entry = {"tokens": {"access_token": "orig_tok", "token_type": "bearer"}}
    creds.write_text(json.dumps({"acme": original_entry}))
    os.chmod(creds, 0o600)

    provider, fake_http = _capture_provider_and_invoke_callback()

    async def run():
        with (
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
            patch("sys.stdin", io.StringIO("")),
        ):
            with pytest.raises(ValueError, match="No URL pasted"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=True)

    asyncio.run(run())
    assert json.loads(creds.read_text())["acme"] == original_entry


def test_login_headless_pasted_url_error_surfaces(tmp_path):
    """A denial pasted back reaches the caller as the shared ValueError."""
    creds = tmp_path / "credentials.json"
    provider, fake_http = _capture_provider_and_invoke_callback()

    async def run():
        with (
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
            patch("sys.stdin", io.StringIO("http://localhost/callback?error=access_denied\n")),
        ):
            with pytest.raises(ValueError, match="access_denied"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=True)

    asyncio.run(run())


def test_login_explicit_headless_false_beats_env(monkeypatch, tmp_path):
    """headless=False wins over MCPGEN_HEADLESS=1 — the argument is the top priority."""
    monkeypatch.setenv("MCPGEN_HEADLESS", "1")
    creds = tmp_path / "credentials.json"
    provider_cls = MagicMock()

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", provider_cls),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=False)

    asyncio.run(run())
    metadata = provider_cls.call_args.kwargs["client_metadata"]
    assert [str(u) for u in metadata.redirect_uris] == ["http://localhost:9999/callback"]


def test_ensure_login_all_runs_servers_in_order_with_kwargs(tmp_path):
    """Sequential by design — parallel logins would race for the browser and stdin."""
    creds = tmp_path / "credentials.json"
    calls = []

    async def fake_ensure_login(name, creds_path=None, **kwargs):
        calls.append((name, creds_path, kwargs))

    async def run():
        with patch("mcpgen._bridge.ensure_login", fake_ensure_login):
            await _bridge.ensure_login_all(
                ["acme", "beta"],
                creds,
                config_path="/tmp/servers.json",
                cred_backend="keyring",
                headless=True,
            )

    asyncio.run(run())
    assert [c[0] for c in calls] == ["acme", "beta"]
    for _, creds_path, kwargs in calls:
        assert creds_path == creds
        assert kwargs == {
            "config_path": "/tmp/servers.json",
            "cred_backend": "keyring",
            "headless": True,
            "callback_timeout": None,
        }


def test_ensure_login_threads_headless_into_login(tmp_path):
    """ensure_login() with no cached token must pass headless through to login()."""
    creds = tmp_path / "credentials.json"
    login_mock = AsyncMock()

    async def run():
        with patch("mcpgen._bridge.login", login_mock):
            await _bridge.ensure_login("acme", creds, url="https://acme.example.com/mcp", headless=True)

    asyncio.run(run())
    assert login_mock.await_args.kwargs["headless"] is True


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--headless"], True), (["--no-headless"], False), ([], None)],
)
def test_cli_login_headless_flag(argv_flag, expected):
    """--headless / --no-headless / absent → True / False / None at _bridge.login."""
    from mcpgen.cli import main

    login_mock = AsyncMock()
    with patch("mcpgen._bridge.login", login_mock):
        assert main(["login", "acme", *argv_flag]) == 0
    assert login_mock.await_args.kwargs["headless"] is expected


def test_cli_login_server_unavailable_is_reported_not_traced(capsys):
    """`mcpgen login` on a down server: one line, nonzero exit, no traceback.

    The credential is good, so the message must not read as "log in again".
    """
    from mcpgen.cli import main

    login_mock = AsyncMock(side_effect=_bridge.PostLoginCheckFailed("acme is not answering (502)"))
    with patch("mcpgen._bridge.login", login_mock):
        assert main(["login", "acme"]) == 1
    assert "502" in capsys.readouterr().err


def test_login_headless_pasted_url_without_code_rejected(tmp_path):
    """A URL pasted without ?code= (e.g. the bare redirect) fails loudly."""
    creds = tmp_path / "credentials.json"
    provider, fake_http = _capture_provider_and_invoke_callback()

    async def run():
        with (
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
            patch("sys.stdin", io.StringIO("http://localhost/callback\n")),
        ):
            with pytest.raises(ValueError, match="no \\?code="):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=True)

    asyncio.run(run())


def test_login_interactive_times_out_when_callback_never_arrives(tmp_path):
    """A browser that never returns must fail, not hang.

    Some authorization servers close the tab on cancel without an error
    redirect, so no callback request ever reaches the local server and the
    future stays pending forever.
    """
    creds = tmp_path / "credentials.json"
    original_entry = {"tokens": {"access_token": "orig_tok", "token_type": "bearer"}}
    creds.write_text(json.dumps({"acme": original_entry}))
    os.chmod(creds, 0o600)

    async def never_resolving_callback_server():
        return 9999, asyncio.get_running_loop().create_future()

    provider, fake_http = _capture_provider_and_invoke_callback()

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", never_resolving_callback_server),
            patch("mcpgen._bridge._CALLBACK_TIMEOUT", 0.01),
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
        ):
            with pytest.raises(TimeoutError) as exc:
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=False)
        assert "--headless" in str(exc.value)

    asyncio.run(run())
    assert json.loads(creds.read_text())["acme"] == original_entry, (
        "a timed-out login must restore the prior credential"
    )


def test_login_headless_stdin_read_is_not_timed_out(tmp_path):
    """The stdin paste must not inherit the browser timeout — humans are slow."""
    creds = tmp_path / "credentials.json"
    captured: dict = {}
    result: dict = {}

    def provider(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    @asynccontextmanager
    async def fake_http(*args, **kwargs):
        result["callback"] = await captured["callback_handler"]()
        raise RuntimeError("callback returned")
        yield  # unreachable

    async def run():
        with (
            patch("mcpgen._bridge._CALLBACK_TIMEOUT", 0.01),
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
            patch("sys.stdin", _SlowStdin("http://localhost/callback?code=abc&state=xyz\n")),
        ):
            with pytest.raises(RuntimeError, match="callback returned"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp", headless=True)

    asyncio.run(run())
    # A TimeoutError instead would mean the stdin read inherited the browser bound.
    assert result["callback"] == ("abc", "xyz")


class _SlowStdin:
    """stdin whose readline() takes longer than the patched callback timeout."""

    def __init__(self, line: str):
        self._line = line

    def readline(self) -> str:
        time.sleep(0.05)
        return self._line


def _never_resolving_callback_server():
    async def fake_callback_server():
        return 9999, asyncio.get_running_loop().create_future()

    return fake_callback_server


def test_login_callback_timeout_overrides_the_constant(tmp_path):
    """An explicit callback_timeout wins over _CALLBACK_TIMEOUT and reaches wait_for."""
    creds = tmp_path / "credentials.json"
    provider, fake_http = _capture_provider_and_invoke_callback()
    seen: dict = {}
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(awaitable, timeout):
        seen["timeout"] = timeout
        return await real_wait_for(awaitable, timeout)

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _never_resolving_callback_server()),
            patch("mcpgen._bridge._CALLBACK_TIMEOUT", 999),
            patch("mcpgen._bridge.asyncio.wait_for", spy_wait_for),
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
        ):
            with pytest.raises(TimeoutError, match="0.01s"):
                await _bridge.login(
                    "acme",
                    creds_path=creds,
                    url="https://acme.example.com/mcp",
                    headless=False,
                    callback_timeout=0.01,
                )

    asyncio.run(run())
    assert seen["timeout"] == 0.01


def test_login_callback_timeout_zero_waits_forever(tmp_path):
    """callback_timeout=0 restores the unbounded wait — no wait_for, no timeout."""
    creds = tmp_path / "credentials.json"
    captured: dict = {}

    def provider(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    @asynccontextmanager
    async def fake_http(*args, **kwargs):
        # The handler must still be pending well past the patched 0.01s bound.
        handler = asyncio.ensure_future(captured["callback_handler"]())
        await asyncio.sleep(0.05)
        assert not handler.done(), "callback_timeout=0 must not time out"
        handler.cancel()
        raise RuntimeError("stopped waiting")
        yield  # unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _never_resolving_callback_server()),
            patch("mcpgen._bridge._CALLBACK_TIMEOUT", 0.01),
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
        ):
            with pytest.raises(RuntimeError, match="stopped waiting"):
                await _bridge.login(
                    "acme",
                    creds_path=creds,
                    url="https://acme.example.com/mcp",
                    headless=False,
                    callback_timeout=0,
                )

    asyncio.run(run())


def test_login_headless_ignores_callback_timeout(tmp_path):
    """The flag must not bound the stdin read — a human paste may take any time."""
    creds = tmp_path / "credentials.json"
    captured: dict = {}
    result: dict = {}

    def provider(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    @asynccontextmanager
    async def fake_http(*args, **kwargs):
        result["callback"] = await captured["callback_handler"]()
        raise RuntimeError("callback returned")
        yield  # unreachable

    async def run():
        with (
            patch("mcpgen._bridge._open_http", fake_http),
            patch("mcpgen._bridge.OAuthClientProvider", provider),
            patch("sys.stdin", _SlowStdin("http://localhost/callback?code=abc&state=xyz\n")),
        ):
            with pytest.raises(RuntimeError, match="callback returned"):
                await _bridge.login(
                    "acme",
                    creds_path=creds,
                    url="https://acme.example.com/mcp",
                    headless=True,
                    callback_timeout=0.01,
                )

    asyncio.run(run())
    assert result["callback"] == ("abc", "xyz")


def test_ensure_login_all_threads_callback_timeout(tmp_path):
    """callback_timeout rides along with headless through both ensure_* helpers."""
    creds = tmp_path / "credentials.json"
    calls = []

    async def fake_ensure_login(name, creds_path=None, **kwargs):
        calls.append(kwargs)

    async def run():
        with patch("mcpgen._bridge.ensure_login", fake_ensure_login):
            await _bridge.ensure_login_all(["acme", "beta"], creds, callback_timeout=42)

    asyncio.run(run())
    assert [c["callback_timeout"] for c in calls] == [42, 42]


def test_ensure_login_threads_callback_timeout_into_login(tmp_path):
    creds = tmp_path / "credentials.json"
    login_mock = AsyncMock()

    async def run():
        with patch("mcpgen._bridge.login", login_mock):
            await _bridge.ensure_login("acme", creds, url="https://acme.example.com/mcp", callback_timeout=42)

    asyncio.run(run())
    assert login_mock.await_args.kwargs["callback_timeout"] == 42


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--callback-timeout", "45"], 45.0), (["--callback-timeout", "0"], 0.0), ([], None)],
)
def test_cli_login_callback_timeout_flag(argv_flag, expected):
    """--callback-timeout reaches _bridge.login; absent → None → module default."""
    from mcpgen.cli import main

    login_mock = AsyncMock()
    with patch("mcpgen._bridge.login", login_mock):
        assert main(["login", "acme", *argv_flag]) == 0
    assert login_mock.await_args.kwargs["callback_timeout"] == expected


@pytest.mark.parametrize("bad", ["-1", "abc", "nan"])
def test_cli_login_callback_timeout_rejects_invalid(bad, capsys):
    """Nonsense values fail at argparse with a usage message, not deep in asyncio."""
    from mcpgen.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["login", "acme", "--callback-timeout", bad])
    assert exc.value.code == 2
    assert "--callback-timeout" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# creds_path on the CALL path (not just login)
# ---------------------------------------------------------------------------


def _recording_storage_factory(recorded: dict):
    """Stand-in for FileTokenStorage that records how it was constructed."""

    def factory(server_name, credentials_path=_bridge.DEFAULT_CREDS_PATH, *, backend=None):
        recorded["server_name"] = server_name
        recorded["credentials_path"] = credentials_path
        recorded["backend"] = backend
        storage = MagicMock()
        storage._load.return_value = {}
        return storage

    return factory


def _run_failing_call(recorded: dict, **caller_kwargs):
    """Drive McpBridgeCaller.call() far enough to construct the token storage."""

    async def run():
        with (
            patch("mcpgen._bridge.FileTokenStorage", _recording_storage_factory(recorded)),
            patch("mcpgen._bridge._pre_flight_refresh", AsyncMock()),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
        ):
            caller = _bridge.McpBridgeCaller(url="https://acme.example.com/mcp", **caller_kwargs)
            with pytest.raises(RuntimeError, match="network error"):
                await caller.call("acme", "whoami", {})

    asyncio.run(run())


def test_call_path_uses_custom_creds_path(tmp_path):
    """A caller-supplied creds_path must reach the token storage on the call path.

    login() honoured creds_path but the session did not, so tokens were written
    to one file and read from another.
    """
    creds = tmp_path / "alt-credentials.json"
    recorded: dict = {}
    _run_failing_call(recorded, creds_path=creds)
    assert recorded["credentials_path"] == creds


def test_call_path_defaults_to_default_creds_path():
    """No creds_path → DEFAULT_CREDS_PATH; existing behaviour is unchanged."""
    recorded: dict = {}
    _run_failing_call(recorded)
    assert recorded["credentials_path"] == _bridge.DEFAULT_CREDS_PATH


def test_session_forwards_creds_path_to_http_session(tmp_path):
    """session() is a public entry point too — it must forward the path."""
    creds = tmp_path / "alt-credentials.json"
    recorded: dict = {}

    async def run():
        with (
            patch("mcpgen._bridge.FileTokenStorage", _recording_storage_factory(recorded)),
            patch("mcpgen._bridge._pre_flight_refresh", AsyncMock()),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
            patch("mcpgen._bridge._open_http", _fake_http_fail),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                async with _bridge.session("acme", url="https://acme.example.com/mcp", creds_path=creds):
                    pass

    asyncio.run(run())
    assert recorded["credentials_path"] == creds


def test_default_creds_path_is_exported():
    """Consumers need the default as a value instead of re-deriving the path."""
    import mcpgen

    assert mcpgen.DEFAULT_CREDS_PATH is _bridge.DEFAULT_CREDS_PATH
    assert "DEFAULT_CREDS_PATH" in mcpgen.__all__


def _fake_session_factory(captured: dict):
    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        captured.update(kwargs)
        s = MagicMock()
        s.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
        yield s

    return fake_session


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--creds", "/tmp/alt-creds.json"], Path("/tmp/alt-creds.json")), ([], None)],
)
def test_cli_creds_flag_reaches_session(argv_flag, expected):
    """`--creds` on a session-opening command arrives at _bridge.session."""
    from mcpgen.cli import main

    captured: dict = {}
    with patch("mcpgen._bridge.session", _fake_session_factory(captured)):
        assert main(["list", "acme", "--url", "https://acme.example.com/mcp", *argv_flag]) == 0
    assert captured["creds_path"] == expected


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--creds", "/tmp/alt-creds.json"], Path("/tmp/alt-creds.json")), ([], _bridge.DEFAULT_CREDS_PATH)],
)
def test_cli_login_passes_creds_path(argv_flag, expected):
    """`mcpgen login` previously dropped --creds entirely — it must log in to that file."""
    from mcpgen.cli import main

    login_mock = AsyncMock()
    with patch("mcpgen._bridge.login", login_mock):
        assert main(["login", "acme", *argv_flag]) == 0
    assert login_mock.await_args.args[1] == expected


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--creds", "/tmp/alt-creds.json"], Path("/tmp/alt-creds.json")), ([], _bridge.DEFAULT_CREDS_PATH)],
)
def test_cli_list_creds_passes_creds_path(argv_flag, expected):
    """`list-creds` must inspect the same store `login --creds` wrote to."""
    from mcpgen.cli import main

    with patch("mcpgen._bridge.list_creds", return_value=[]) as list_mock:
        assert main(["list-creds", *argv_flag]) == 0
    assert list_mock.call_args.kwargs["credentials_path"] == expected


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--creds", "/tmp/alt-creds.json"], Path("/tmp/alt-creds.json")), ([], _bridge.DEFAULT_CREDS_PATH)],
)
def test_cli_delete_creds_passes_creds_path(argv_flag, expected):
    """Deleting from the default store when the user named another would be a silent no-op."""
    from mcpgen.cli import main

    with patch("mcpgen._bridge.delete_cred", return_value=True) as delete_mock:
        assert main(["delete-creds", "acme", "--yes", *argv_flag]) == 0
    assert delete_mock.call_args.kwargs["credentials_path"] == expected


@pytest.mark.parametrize(
    ("argv_flag", "expected"),
    [(["--creds", "/tmp/alt-creds.json"], Path("/tmp/alt-creds.json")), ([], _bridge.DEFAULT_CREDS_PATH)],
)
def test_cli_migrate_creds_passes_creds_path(argv_flag, expected):
    """migrate-creds reads and writes the file backend — it needs the path too."""
    from mcpgen.cli import main

    result = {"from": "file", "to": "keyring", "migrated": 0, "overwritten": 0, "purged": False, "set_default": False}
    with patch("mcpgen._bridge.migrate_creds", return_value=result) as migrate_mock:
        assert main(["migrate-creds", "--from", "file", "--to", "keyring", *argv_flag]) == 0
    assert migrate_mock.call_args.kwargs["credentials_path"] == expected


# ---------------------------------------------------------------------------
# SSE: discovered, not supported
# ---------------------------------------------------------------------------


def test_session_rejects_config_declared_sse_server(tmp_path):
    """An SSE server must fail fast with a clear message, not silently take the
    Streamable HTTP path and produce an opaque transport error.

    Hermetic: _http_session is patched to raise, so a regression fails here
    instead of attempting real DNS from an otherwise network-free suite.
    """
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"legacy": {"type": "sse", "url": "https://x.example/sse"}}}))

    @asynccontextmanager
    async def unreachable_http_session(*args, **kwargs):
        raise AssertionError("SSE entry reached the Streamable HTTP transport")
        yield  # pragma: no cover — makes this a generator

    async def run():
        with patch("mcpgen._bridge._http_session", unreachable_http_session):
            async with _bridge.session("legacy", config_path=str(config)):
                pass  # pragma: no cover — must raise before yielding

    with pytest.raises(ValueError, match="SSE transport"):
        asyncio.run(run())


def test_session_allows_config_declared_http_server(tmp_path):
    """The guard must be narrow: only type=='sse' is refused."""
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"modern": {"type": "http", "url": "https://x.example/mcp"}}}))
    opened: dict = {}

    @asynccontextmanager
    async def fake_http_session(server_name, server_url, **kwargs):
        opened["url"] = server_url
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge._http_session", fake_http_session):
            async with _bridge.session("modern", config_path=str(config)):
                pass

    asyncio.run(run())
    assert opened["url"] == "https://x.example/mcp"


def test_session_sse_guard_does_not_block_stdio_override(tmp_path):
    """--stdio explicitly overrides config, so the SSE guard must not fire."""
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"legacy": {"type": "sse", "url": "https://x.example/sse"}}}))
    started: dict = {}

    @asynccontextmanager
    async def fake_stdio_session(command, args, env=None):
        started["command"] = command
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge._stdio_session", fake_stdio_session):
            async with _bridge.session("legacy", cmd="python srv.py", config_path=str(config)):
                pass

    asyncio.run(run())
    assert started["command"] == "python"


# ---------------------------------------------------------------------------
# Session reuse: block lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _tracking_session(log: list, name: str = "s"):
    """A fake session context manager that records the task it enters/exits in."""
    log.append(("enter", name, asyncio.current_task()))
    try:
        yield _make_mock_session({"ok": name})
    finally:
        log.append(("exit", name, asyncio.current_task()))


def test_connected_opens_one_session_for_two_calls():
    """Two calls in one block share a single session (one initialize)."""
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                await caller.call("demo", "greet", {})
                await caller.call("demo", "add", {})

    asyncio.run(run())
    assert [e[0] for e in log] == ["enter", "exit"]


def test_one_shot_calls_still_open_a_session_each():
    """Outside a block, behaviour is unchanged: one session per call."""
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            await caller.call("demo", "greet", {})
            await caller.call("demo", "add", {})

    asyncio.run(run())
    assert [e[0] for e in log] == ["enter", "exit", "enter", "exit"]


def test_connected_returns_the_caller():
    """`async with caller.connected() as c` yields the same caller, for convenience."""

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected() as c:
                assert c is caller

    asyncio.run(run())


def test_connected_closes_session_on_exit():
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                await caller.call("demo", "greet", {})

    asyncio.run(run())
    assert log[-1][0] == "exit"


def test_connected_closes_session_when_body_raises():
    """Cleanup on the exception path — the audit's constraint 4."""
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                await caller.call("demo", "greet", {})
                raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run())
    assert log[-1][0] == "exit"


def test_connected_enter_and_exit_happen_in_the_same_task():
    """The anyio cancel-scope rule: whatever task triggers the open, the context
    manager must be entered and exited by one and the same task."""
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                # Open from a child task, close from the parent — this is the
                # combination that breaks a naive AsyncExitStack.
                await asyncio.gather(caller.call("demo", "greet", {}))

    asyncio.run(run())
    enter_task = next(e[2] for e in log if e[0] == "enter")
    exit_task = next(e[2] for e in log if e[0] == "exit")
    assert enter_task is exit_task


def test_connected_is_not_reentrant():
    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                async with caller.connected():
                    pass  # pragma: no cover

    with pytest.raises(RuntimeError, match="not re-entrant"):
        asyncio.run(run())


def test_block_state_is_cleared_after_exit():
    """After a block, the caller is one-shot again — including after an exception."""
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            try:
                async with caller.connected():
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            await caller.call("demo", "greet", {})
            await caller.call("demo", "add", {})

    asyncio.run(run())
    # The failed block opened nothing; the two one-shot calls opened one each.
    assert [e[0] for e in log] == ["enter", "exit", "enter", "exit"]


def test_connected_reuses_session_across_calls_and_returns_results():
    calls: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        s = MagicMock()

        async def call_tool(tool, arguments):
            calls.append(tool)
            return MagicMock(content=[SimpleNamespace(type="text", text=json.dumps({"tool": tool}))])

        s.call_tool = call_tool
        yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                first = await caller.call("demo", "greet", {})
                second = await caller.call("demo", "add", {})
            return first, second

    first, second = asyncio.run(run())
    assert first == {"tool": "greet"}
    assert second == {"tool": "add"}
    assert calls == ["greet", "add"]


def test_connected_opens_one_session_per_distinct_server():
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller()
            async with caller.connected():
                await caller.call("alpha", "t", {})
                await caller.call("beta", "t", {})
                await caller.call("alpha", "t", {})

    asyncio.run(run())
    opened = [e[1] for e in log if e[0] == "enter"]
    closed = [e[1] for e in log if e[0] == "exit"]
    assert sorted(opened) == ["alpha", "beta"]
    assert sorted(closed) == ["alpha", "beta"]


def test_connected_concurrent_first_calls_open_one_session():
    """The open lock: two concurrent first-calls must not start two subprocesses."""
    log: list = []

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        await asyncio.sleep(0)  # yield control, widening the race window
        async with _tracking_session(log, server) as s:
            yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                return await asyncio.gather(
                    caller.call("demo", "greet", {}),
                    caller.call("demo", "add", {}),
                )

    results = asyncio.run(run())
    assert len([e for e in log if e[0] == "enter"]) == 1
    assert len(results) == 2


def test_connected_concurrent_calls_are_not_serialized():
    """call_tool() must run unserialized so gather() stays genuinely concurrent."""
    in_flight = 0
    peak = 0

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        s = MagicMock()

        async def call_tool(tool, arguments):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return MagicMock(content=[SimpleNamespace(type="text", text="{}")])

        s.call_tool = call_tool
        yield s

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                await caller.call("demo", "warmup", {})  # open the session first
                await asyncio.gather(
                    caller.call("demo", "a", {}),
                    caller.call("demo", "b", {}),
                    caller.call("demo", "c", {}),
                )

    asyncio.run(run())
    assert peak >= 2


def test_connected_open_failure_propagates_to_the_caller():
    """A transport error on open surfaces at the call site, not as a hang."""

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        raise ConnectionError("refused")
        yield  # pragma: no cover

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                await caller.call("demo", "greet", {})

    with pytest.raises(ConnectionError, match="refused"):
        asyncio.run(run())


def test_connected_open_failure_does_not_wedge_the_block():
    """After a failed open, the block still closes cleanly."""

    attempts = {"n": 0}

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("refused")
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge.session", fake_session):
            caller = _bridge.McpBridgeCaller(cmd="python srv.py")
            async with caller.connected():
                with pytest.raises(ConnectionError):
                    await caller.call("demo", "greet", {})
                return await caller.call("demo", "add", {})

    assert asyncio.run(run()) == {"ok": True}


def test_connected_is_public_api():
    """`connected` is part of the documented surface, not an internal."""
    from mcpgen import McpBridgeCaller

    assert hasattr(McpBridgeCaller, "connected")
    assert McpBridgeCaller.connected.__doc__


def test_pre_flight_refresh_runs_once_per_block():
    """OAuth pre-flight must fire per block, not per call (audit §4 constraint 6)."""
    refreshes = {"n": 0}

    @asynccontextmanager
    async def fake_http_session(server_name, server_url, **kwargs):
        refreshes["n"] += 1
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge._http_session", fake_http_session):
            with patch("mcpgen._bridge.servers", return_value={"acme": "https://acme.example/mcp"}):
                caller = _bridge.McpBridgeCaller()
                async with caller.connected():
                    await caller.call("acme", "a", {})
                    await caller.call("acme", "b", {})
                    await caller.call("acme", "c", {})

    asyncio.run(run())
    # _http_session is where _pre_flight_refresh lives; entering it once per
    # block means one refresh, not three.
    assert refreshes["n"] == 1


def test_session_rejects_sse_from_the_default_config_search_path(tmp_path, monkeypatch):
    """The guard must work without --config — that is how real users hit it."""
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"legacy": {"type": "sse", "url": "https://x.example/sse"}}}))
    # Start from an empty cache, otherwise an earlier config_path= test leaves
    # {"legacy": "sse"} behind and this passes without ever exercising the
    # search-order assignment it exists to cover.
    monkeypatch.setattr(_bridge, "_types_cache", {})
    monkeypatch.setattr(_bridge, "_servers_cache", None)
    monkeypatch.setenv(_bridge._SERVERS_CONFIG_ENV, str(config))
    _bridge.servers(refresh=True)
    assert _bridge._types_cache == {"legacy": "sse"}

    async def run():
        async with _bridge.session("legacy"):
            pass  # pragma: no cover

    with pytest.raises(ValueError, match="SSE transport"):
        asyncio.run(run())


def test_types_cache_is_cleared_when_no_config_is_found(tmp_path, monkeypatch):
    """A stale _types_cache would refuse a server that is no longer SSE."""
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"legacy": {"type": "sse", "url": "https://x.example/sse"}}}))
    monkeypatch.setattr(_bridge, "_types_cache", {})
    monkeypatch.setattr(_bridge, "_servers_cache", None)
    monkeypatch.setenv(_bridge._SERVERS_CONFIG_ENV, str(config))
    _bridge.servers(refresh=True)
    assert _bridge._types_cache == {"legacy": "sse"}

    monkeypatch.setenv(_bridge._SERVERS_CONFIG_ENV, str(tmp_path / "absent.json"))
    monkeypatch.setattr(_bridge, "_SERVERS_SEARCH", [])
    _bridge.servers(refresh=True)

    # Assert both the cache state and the observable behaviour: the first alone
    # tests the assignment, the second alone tests the guard.
    assert _bridge._types_cache == {}

    opened: dict = {}

    @asynccontextmanager
    async def fake_http_session(server_name, server_url, **kwargs):
        opened["hit"] = True
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge._http_session", fake_http_session):
            with patch("mcpgen._bridge.servers", return_value={"legacy": "https://x.example/mcp"}):
                async with _bridge.session("legacy"):
                    pass

    asyncio.run(run())
    assert opened.get("hit") is True


def test_bearer_does_not_bypass_the_sse_guard(tmp_path):
    """--bearer is an auth override, not a transport override.

    Without this, `mcpgen list legacy --bearer $TOK` skips the guard, resolves
    the SSE URL from that same config entry, and routes it into _bearer_session
    → Streamable HTTP → the opaque error the guard exists to prevent.
    """
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"legacy": {"type": "sse", "url": "https://x.example/sse"}}}))

    async def run():
        async with _bridge.session("legacy", bearer="tok", config_path=str(config)):
            pass  # pragma: no cover

    with pytest.raises(ValueError, match="SSE transport"):
        asyncio.run(run())


def test_url_override_bypasses_the_sse_guard(tmp_path):
    """--url re-targets the transport, so it must still be exempt."""
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({"mcpServers": {"legacy": {"type": "sse", "url": "https://x.example/sse"}}}))
    opened: dict = {}

    @asynccontextmanager
    async def fake_http_session(server_name, server_url, **kwargs):
        opened["url"] = server_url
        yield _make_mock_session({"ok": True})

    async def run():
        with patch("mcpgen._bridge._http_session", fake_http_session):
            async with _bridge.session("legacy", url="https://x.example/mcp", config_path=str(config)):
                pass

    asyncio.run(run())
    assert opened["url"] == "https://x.example/mcp"


# ---------------------------------------------------------------------------
# parse(): multi-block results must not lose data
# ---------------------------------------------------------------------------


def test_parse_multiple_text_blocks_returns_a_list():
    """MCP serializes a list return as one content block per element."""
    blocks = [
        {"type": "text", "text": '{"id": 1, "name": "record-1"}'},
        {"type": "text", "text": '{"id": 2, "name": "record-2"}'},
        {"type": "text", "text": '{"id": 3, "name": "record-3"}'},
    ]
    assert _bridge.parse(blocks) == [
        {"id": 1, "name": "record-1"},
        {"id": 2, "name": "record-2"},
        {"id": 3, "name": "record-3"},
    ]


def test_parse_single_block_is_unchanged():
    """The common case must stay byte-compatible — no list wrapping."""
    assert _bridge.parse([{"type": "text", "text": '{"a": 1}'}]) == {"a": 1}


def test_parse_single_block_scalar_is_unchanged():
    assert _bridge.parse([{"type": "text", "text": "42"}]) == 42


def test_parse_multiple_plain_string_blocks():
    """Non-JSON text blocks fold into a list of strings rather than vanishing."""
    blocks = [{"type": "text", "text": "alpha"}, {"type": "text", "text": "beta"}]
    assert _bridge.parse(blocks) == ["alpha", "beta"]


def test_parse_multiple_blocks_of_mixed_types():
    """A text block plus an image block keeps both, in order."""
    blocks = [
        {"type": "text", "text": '{"a": 1}'},
        {"type": "image", "mimeType": "image/png", "data": "…"},
    ]
    result = _bridge.parse(blocks)
    assert result[0] == {"a": 1}
    assert result[1]["type"] == "image"


def test_parse_empty_still_raises():
    with pytest.raises(ValueError, match="empty content"):
        _bridge.parse([])


def test_parse_single_image_block_is_unchanged():
    block = {"type": "image", "mimeType": "image/png", "data": "…"}
    assert _bridge.parse([block]) == block


def test_parse_multiple_python_repr_blocks():
    """The ast.literal_eval fallback applies per block, not just to the first."""
    blocks = [{"type": "text", "text": "{'a': 1}"}, {"type": "text", "text": "{'b': 2}"}]
    assert _bridge.parse(blocks) == [{"a": 1}, {"b": 2}]
