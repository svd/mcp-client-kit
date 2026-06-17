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
        with patch("mcpgen._bridge._open_http", fake_http), \
             patch("mcpgen._bridge.ClientSession") as mock_cs:
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
        with patch("mcpgen._bridge._open_http", fake_http), \
             patch("mcpgen._bridge.ClientSession") as mock_cs, \
             patch("mcpgen._bridge.DEFAULT_CREDS_PATH", creds):
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
        with patch("mcpgen._bridge._bearer_session", fake_bearer), \
             patch("mcpgen._bridge._http_session", fake_oauth), \
             patch("mcpgen._bridge.servers", return_value={}):
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
        with patch("mcpgen._bridge._bearer_session", fake_bearer), \
             patch("mcpgen._bridge.servers", return_value={}):
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
        with patch("mcpgen._bridge._http_session", fake_oauth), \
             patch("mcpgen._bridge.servers",
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
        with patch("mcpgen._bridge._open_http", fake_open_http), \
             patch("mcpgen._bridge.ClientSession") as mock_cs, \
             patch("mcpgen._bridge.servers", return_value={}):
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
    return {
        name: {"tokens": {"access_token": f"tok_{name}", "token_type": "bearer"}}
        for name in server_names
    }


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
        "beta":  {"tokens": {"access_token": "beta_tok", "token_type": "bearer"}},
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
        result = _bridge.migrate_creds(
            "file", "keyring", servers=["acme", "beta"], credentials_path=creds
        )
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
        _bridge.migrate_creds(
            "file", "keyring", servers=["acme"], credentials_path=creds, purge=True
        )

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
            _bridge.migrate_creds(
                "file", "keyring", servers=["acme", "nosuchserver"], credentials_path=creds
            )
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
        result = _bridge.migrate_creds(
            "file", "keyring", credentials_path=creds, set_default=True, config_path=cfg
        )

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
        _bridge.migrate_creds(
            "file", "keyring", credentials_path=creds, set_default=True, config_path=cfg
        )

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
        def get_password(self, s, u): raise RuntimeError("no keyring")
        def set_password(self, s, u, p): raise RuntimeError("no keyring")
        def delete_password(self, s, u): raise RuntimeError("no keyring")

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
        past_name:   {"tokens": {"access_token": "tok_past",   "token_type": "bearer", "expires_at": now - 3600}},
        future_name: {"tokens": {"access_token": "tok_future", "token_type": "bearer", "expires_at": now + 3600}},
        noexp_name:  {"tokens": {"access_token": "tok_noexp",  "token_type": "bearer"}},
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
        "with_rt": {"tokens": {"access_token": "t", "token_type": "bearer",
                                "refresh_token": "r", "expires_at": int(time.time()) + 7200}},
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
        existed = _bridge.delete_cred("svcX", backend="keyring",
                                      credentials_path=tmp_path / "c.json")
        remaining = _bridge._keyring_read_raw()

    assert existed is True
    assert "svcX" not in remaining
    assert "svcY" in remaining


def test_delete_cred_last_keyring_clears(tmp_path):
    """delete_cred of last keyring entry calls _keyring_clear_raw (no residual key)."""
    fake_kr = _FakeKeyringMig()
    with patch.dict("sys.modules", {"keyring": fake_kr}):
        _bridge._keyring_write_raw(_creds_data("solo"))
        existed = _bridge.delete_cred("solo", backend="keyring",
                                      credentials_path=tmp_path / "c.json")
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
        def get_password(self, s, u): return None
        def set_password(self, s, u, p): pass
        def delete_password(self, s, u): raise RuntimeError("keychain locked")

    with patch.dict("sys.modules", {"keyring": _LockedKeyring()}):
        with pytest.raises(RuntimeError, match="keychain locked"):
            _bridge._keyring_clear_raw()


def test_keyring_clear_raw_silent_on_not_found(tmp_path):
    """_keyring_clear_raw is a no-op (no raise) when the entry is absent."""
    class _EmptyKeyring:
        class errors:
            class PasswordDeleteError(Exception):
                pass
        def get_password(self, s, u): return None
        def set_password(self, s, u, p): pass
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
        with patch("mcpgen._bridge._local_callback_server", fake_callback_server), \
             patch("mcpgen._bridge._open_http", fake_http_fail), \
             patch("mcpgen._bridge.OAuthClientProvider", MagicMock()):
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
        with patch("mcpgen._bridge._local_callback_server", fake_callback_server), \
             patch("mcpgen._bridge._open_http", fake_http_fail), \
             patch("mcpgen._bridge.OAuthClientProvider", MagicMock()):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("newserver", creds_path=creds,
                                    url="https://new.example.com/mcp")

    asyncio.run(run())
    # Either no file, or file exists but "newserver" is absent.
    if creds.exists():
        data = json.loads(creds.read_text())
        assert "newserver" not in data


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
