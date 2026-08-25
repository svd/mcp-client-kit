"""Unit tests for _bridge transport routing.

Tests the bearer-token path in session() and McpBridgeCaller without making
real network connections. We mock _open_http and patch the internal
async context-manager helpers so routing logic is exercised in pure Python.

Async helpers are invoked via asyncio.run() (matching the project convention —
no pytest-asyncio dependency needed).
"""

from __future__ import annotations

import asyncio
import errno as _errno
import io
import itertools
import json
import os
import stat
import sys
import threading
import time
import traceback
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.client.auth import OAuthRegistrationError
from mcp.shared.auth import OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from mcpgen import _bridge


@pytest.fixture(autouse=True)
def _keyring_lock_under_tmp(tmp_path, monkeypatch):
    """Keep the keyring store lock out of the developer's real ``~/.mcpgen``.

    The keyring is one global item, so its lock path is a module constant rather
    than something derived from ``--creds`` — which means every keyring-backend
    test, and every ``migrate_creds`` test, would otherwise create and block on a
    lock in the real home directory. That is both a suite that escapes ``tmp_path``
    and the one real flake vector here: under xdist, two CI jobs on one box, or a
    developer running ``mcpgen login`` while the suite runs, a worker blocks on that
    machine-global lock until ``_run_concurrently``'s join times out and reports a
    deadlock that is not one. Autouse, because the trap is the tests that do *not*
    think they touch the keyring lock.
    """
    monkeypatch.setattr(_bridge, "_KEYRING_LOCK_PATH", tmp_path / "keyring-store")


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


def test_login_survives_a_credential_store_that_cannot_be_written(tmp_path):
    """A restore that cannot happen must not become the error the operator sees.

    Guarding the re-read alone left the adjacent door open: the restore right after it
    calls `_save`, and a keyring backend that has started refusing, a full disk, or a
    permission change fails on write at least as readily as on read. The save error
    would then replace the original failure — the same masking bug, one line down.

    The stash is a nicety; the original failure is the answer. Losing the restore is
    survivable (the credential that could not be written was already unusable enough
    to be worth re-authenticating), losing the diagnosis is not.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "orig_tok"}}}))
    os.chmod(creds, 0o600)

    async def fake_callback_server():
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(("code", "state"))
        return 9999, fut

    @asynccontextmanager
    async def fake_http_fail(*args, **kwargs):
        raise RuntimeError("network error")
        yield  # makes this an async generator; unreachable

    real_save = _bridge.FileTokenStorage._save

    def refuses_to_write_the_restore(self, data):
        # Keyed on what is being written, not on call count: the stash-clearing save
        # before the flow must succeed, and only the restore in the handler must fail.
        if data.get("acme", {}).get("tokens", {}).get("access_token") == "orig_tok":
            raise OSError("Read-only file system")
        real_save(self, data)

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", fake_callback_server),
            patch("mcpgen._bridge._open_http", fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
            patch.object(_bridge.FileTokenStorage, "_save", refuses_to_write_the_restore),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())


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

    # `from None` suppresses the *rendering* of the original, because the SDK reports a
    # token response that failed validation by quoting the body into the exception text —
    # so a printed traceback was a second way to leak what the message redacts. The object
    # is still reachable at `__context__` for anything inspecting it programmatically;
    # only the default rendering goes.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is original
    assert excinfo.value.__suppress_context__ is True
    assert "502 Bad Gateway" in str(excinfo.value)


def _sdk_invalid_token_response(body):
    """The SDK's own `OAuthTokenError` over a real pydantic error, not an imitation.

    `mcp/client/auth/utils.py` reports a token response that fails validation as
    `OAuthTokenError(f"Invalid token response: {e}")`. Building it from a genuine
    `ValidationError` is the point — the leak is pydantic's `input_value` repr, and a
    hand-written string would pin the test to a spelling pydantic might not produce.
    """
    from mcp.client.auth import OAuthTokenError

    try:
        OAuthToken(**body)
    except Exception as exc:  # noqa: BLE001 — whatever pydantic raises is what the SDK quotes
        return OAuthTokenError(f"Invalid token response: {exc}")
    raise AssertionError("body validated; it was supposed to fail")


@pytest.mark.parametrize("wrapped", [False, True], ids=["bare", "in-task-group"])
def test_login_does_not_print_a_credential_the_sdk_quoted_back(tmp_path, wrapped):
    """The same leak as the pre-flight one, one function over and through the SDK.

    `_pre_flight_refresh` parses the token itself, so naming the pydantic error by type
    was enough there. On the login path the SDK parses it and reports the failure by
    interpolating that error — `input_value={'accessToken': 'ya29…'}`, a Python repr with
    single quotes, which neither the JSON nor the form regex can see. `_describe` put that
    straight into `PostLoginCheckFailed`, and `cli.py` prints it to stderr.

    Short secrets deliberately: pydantic truncates the quoted repr, so a long token leaks
    a prefix and only a short one leaks whole — a long token would let this pass with the
    redaction removed. The task-group case is the real arrival shape, and it also pins that
    redaction runs per *leaf*: `_describe` recurses before it joins.
    """
    creds = tmp_path / "credentials.json"
    # camelCase on purpose — a gateway re-serialising is exactly how a body reaches the
    # SDK's parser without an `access_token` member to satisfy it.
    sdk_exc = _sdk_invalid_token_response({"accessToken": "SECRET1", "refreshToken": "SECRET2"})
    failure = ExceptionGroup("unhandled errors in a TaskGroup", [sdk_exc]) if wrapped else sdk_exc
    run = _run_login_failing_after_exchange(creds, failure, {"access_token": "fresh_tok"})

    with pytest.raises(_bridge.PostLoginCheckFailed) as excinfo:
        asyncio.run(run())

    exc = excinfo.value
    message = str(exc)
    assert "SECRET1" not in message and "SECRET2" not in message
    assert "<redacted>" in message
    assert "Invalid token response" in message, "the operator still has to see what failed"
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert "SECRET1" not in rendered and "SECRET2" not in rendered


def test_explain_registration_error_does_not_pass_a_client_secret_through(tmp_path):
    """The registration response is the other body that carries a credential.

    RFC 7591 §3.2.1 puts `client_secret` in it, and the SDK reports a 2xx registration
    body that fails validation the same way it reports a token one. `client_secret` is the
    member `_SECRET_MEMBERS` singles out as outliving every token, and it does not expire
    on its own.

    Redacting inside `_explain_registration_error` rather than at the raise site is what
    covers both of its exits — the annotated `invalid_client_metadata` message and the
    pass-through one. `OAuthRegistrationError` is outside the `LoginWontHelp` taxonomy, so
    it escapes both `_cmd_login`'s catch and `main()`'s roots: a traceback is its ordinary
    rendering, not its unlucky one, which is why the raise site also drops the chain.
    """
    quoted = (
        "Invalid registration response: 1 validation error for OAuthClientInformationFull\n"
        "client_id\n  Field required [type=missing, "
        "input_value={'clientSecret': 'SECRET_CS'}, input_type=dict]"
    )

    plain = _bridge._explain_registration_error(OAuthRegistrationError(quoted))
    assert "SECRET_CS" not in str(plain)
    assert "<redacted>" in str(plain)

    # The annotated exit: same redaction, and the annotation still attaches.
    annotated = _bridge._explain_registration_error(OAuthRegistrationError(f"invalid_client_metadata\n{quoted}"))
    assert "SECRET_CS" not in str(annotated)
    assert "public client" in str(annotated), "the annotation is the reason this function exists"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{'access_token': 'abc'}", "{'access_token': '<redacted>'}"),
        ('{"access_token": "abc"}', '{"access_token": "<redacted>"}'),
        ("{'accessToken': 'abc', 'refresh-token': 'def'}", None),
        ("{'access_token': 'abc", "{'access_token': '<redacted>'"),
        ("access_token='abc'", "'access_token': '<redacted>'"),
        ("error_description='access_token was rejected'", "error_description='access_token was rejected'"),
    ],
    ids=["repr", "json-untouched-by-repr-pattern", "repr-camel-and-kebab", "repr-truncated", "kwarg", "prose"],
)
def test_redact_secret_text_covers_every_spelling_without_eating_prose(text, expected):
    """Three shapes, one helper, and one ordering constraint that is easy to get wrong.

    `kwarg` is the case that pins repr-before-form: `_SECRET_FORM_RE` matches
    `access_token=` and stops at the quote, so running it first yields
    `access_token=<redacted>'abc'` — a substitution that reads as redacted and still
    carries the credential.

    `prose` is the guard in the other direction. The pattern demands a separator and an
    opening quote after the member name, so a message that merely *names* one survives —
    which is what keeps the excerpt worth printing.

    `repr-truncated` is ordinary rather than exotic here: pydantic cuts that repr at
    roughly fifty characters, so a value severed mid-token is the common case.
    """
    out = _bridge._redact_secret_text(text)
    if expected is None:
        assert "abc" not in out and "def" not in out
        assert out.count("<redacted>") == 2
    else:
        assert out == expected


def test_redact_secret_text_drops_a_pydantic_frame_truncated_mid_key():
    """The reproduced spelling, frozen as a regression case.

    Pydantic cuts the quoted repr in the middle, and the cut lands mid-*key* as readily as
    mid-value: the second member below reads `efreshToken`, having lost its `r`, so no
    member pattern can match it while its value sits there intact. Redacting the frame is
    what makes the outcome independent of where the cut fell.
    """
    text = (
        "Field required [type=missing, "
        "input_value={'accessToken': 'SECRET1'...efreshToken': 'SECRET2'}, input_type=dict]"
    )
    out = _bridge._redact_secret_text(text)

    assert "SECRET1" not in out and "SECRET2" not in out
    # What the reader actually needs survives: which field, which constraint, which type.
    assert "type=missing" in out and "input_type=dict" in out


@pytest.mark.parametrize("pad", [0, 1, 7, 23, 41, 59])
def test_redact_secret_text_holds_wherever_pydantic_cuts(pad):
    """Generated from pydantic, not from a hardcoded guess at how it truncates.

    The cut point is a function of the total repr length, which the *server* controls — a
    `scope` string is enough to move it. Sweeping the padding walks the ellipsis across the
    key, and a fix that depends on where it lands fails somewhere in this range.
    """
    body = {"scope": "x" * pad, "refresh_token": "SECRETX"}
    with pytest.raises(Exception) as excinfo:  # noqa: B017 — pydantic's own type, whatever it is
        OAuthToken(**body)

    assert "SECRETX" not in _bridge._redact_secret_text(str(excinfo.value))


@pytest.mark.parametrize(
    "value",
    ["not a dict", 123, ["a"]],
    ids=["string", "int", "list"],
)
def test_redact_secret_text_handles_every_input_value_shape(value):
    """`input_value=` is not always a dict, and the terminator is what makes that moot.

    A field-level failure prints a bare string; a non-dict body prints whatever it was.
    The lazy match runs to `, input_type=` regardless, so no per-shape case is needed —
    and the frame around it has to survive, or the message stops saying anything.
    """
    with pytest.raises(Exception) as excinfo:  # noqa: B017 — pydantic's own type
        OAuthToken.model_validate(value)

    out = _bridge._redact_secret_text(str(excinfo.value))
    assert "input_value=<redacted>" in out
    assert "input_type=" in out


def test_redact_secret_text_bounds_a_frame_that_arrives_cut_short():
    """A message some upstream wrapper already truncated has no `, input_type=` to find.

    Without the end-of-line fallback the match fails and the frame prints verbatim —
    which is the one case where the value is guaranteed to be the tail of the text.
    """
    out = _bridge._redact_secret_text("1 validation error … input_value={'refresh_token': 'SECR")
    assert "SECR" not in out


def test_redact_secret_text_bounds_an_unterminated_frame_at_the_line_end():
    """The `re.M` on the frame pattern, which nothing else pins.

    `.` excludes newline, so without multiline the `$` fallback cannot match at a line end
    *inside* the string — an unterminated frame with anything after it stops being redacted
    at all, and what survives is the mid-key case the pattern exists for. Dropping `re.M`
    passes every other test in this file.
    """
    text = "1 validation error\n  x [input_value={'accessToken': 'S1'...efreshToken': 'SECRET2'\nsee also: docs"
    out = _bridge._redact_secret_text(text)

    assert "SECRET2" not in out
    assert "see also: docs" in out, "the fallback bounds the match at the line, not the text"


def test_body_excerpt_keeps_a_server_error_that_merely_says_input_value(tmp_path):
    """The frame pattern is scoped to our own exception messages, and this is why.

    A response body is a different corpus: it never carries a pydantic frame this module
    produced. An authorization server backed by pydantic that puts `str(validation_error)`
    into its own `error_description` would otherwise lose the rest of that line — the hint,
    a support reference — to a pattern with nothing to do there.
    """
    creds = _refreshable_creds(tmp_path)
    body = {
        "error": "invalid_request",
        "error_description": "validation failed: input_value='foo' is not an int",
        "hint": "send an integer",
    }

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(400, json_body=body))())

    message = str(excinfo.value)
    assert "send an integer" in message
    assert "is not an int" in message


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
    endpoint = _token_endpoint_replying(
        200, json_body={"access_token": "renewed_tok", "token_type": "bearer"}, record=posted
    )

    asyncio.run(_run_pre_flight(creds, endpoint)())

    assert posted["url"] == "https://auth.example.com/token"
    assert posted["data"]["refresh_token"] == "fresh_refresh", "the kept grant is what gets renewed"
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "renewed_tok"


# ---------------------------------------------------------------------------
# _pre_flight_refresh() — classifying what comes back from the token endpoint
# ---------------------------------------------------------------------------


def _login_that_writes(creds, expires_in=3600):
    """Stand-in for `login()` that does what a real one does: leave a token behind.

    `ensure_login` reads the store after `login()` returns, so a mock that writes
    nothing is a login that did not take — which is a distinct case with its own tests.
    Each call writes a different token, since an unchanged store reads the same way.
    `expires_in=None` writes no `expires_at` at all, the shape `_serialize_tokens`
    produces for a token endpoint that omits the lifetime.
    """
    counter = itertools.count(1)

    def _write(name, path=None, **kwargs):
        n = next(counter)
        data = json.loads(creds.read_text()) if creds.exists() else {}
        data.setdefault(name, {})["tokens"] = {
            "access_token": f"tok{n}",
            "refresh_token": f"rt{n}",
            # `expires_in` alongside `expires_at`, as `_serialize_tokens` writes them: the
            # lifetime the endpoint reported is what tells a too-short token apart from one
            # whose slack the post-login check spent.
            **({} if expires_in is None else {"expires_in": expires_in, "expires_at": int(time.time()) + expires_in}),
        }
        creds.write_text(json.dumps(data))
        os.chmod(creds, 0o600)

    return AsyncMock(side_effect=_write)


def _refreshable_creds(tmp_path, token_endpoint="https://auth.example.com/token"):
    """A credential that is past expiry and has everything needed to renew itself."""
    creds = tmp_path / "credentials.json"
    creds.write_text(
        json.dumps(
            {
                "acme": {
                    "tokens": {
                        "access_token": "stale_tok",
                        "refresh_token": "live_refresh",
                        "expires_at": 1,  # long past; forces the refresh
                    },
                    "client_info": {"client_id": "acme_id"},
                    "token_endpoint": token_endpoint,
                }
            }
        )
    )
    os.chmod(creds, 0o600)
    return creds


class _CaseInsensitiveHeaders(dict):
    """The one behaviour of `httpx.Headers` the classification depends on."""

    def get(self, key, default=None):
        return super().get(key.lower(), default)


def _token_endpoint_replying(status_code, body="", json_body=None, record=None, headers=None):
    """Fake httpx.AsyncClient whose token-endpoint POST answers with *status_code*.

    `.json()` parses `.text` the way httpx does — so a non-JSON body raises rather
    than yielding an empty dict. The classification reads the body shape to tell an
    authorization server apart from a WAF in front of one, so a fake that quietly
    turned an HTML error page into `{}` would let those tests pass for free.

    `.headers` is case-insensitive on lookup, as httpx's is: the code reads
    `retry-after` while servers send `Retry-After`, and a plain dict would make that
    silently miss.

    Pass *record* a dict to capture the posted url, form data, and request headers.
    """
    text = json.dumps(json_body) if json_body is not None else body
    sent_headers = {k.lower(): v for k, v in (headers or {}).items()}

    class _FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.text = text
            self.headers = _CaseInsensitiveHeaders(sent_headers)

        @staticmethod
        def json():
            return json.loads(text)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data, headers=None):
            if record is not None:
                record["url"] = url
                record["data"] = data
                record["headers"] = headers
            return _FakeResponse()

    return _FakeClient


def _run_pre_flight(creds, fake_client):
    """Drive _pre_flight_refresh against *creds* with httpx.AsyncClient faked out."""

    async def run():
        storage = _bridge.FileTokenStorage("acme", creds)
        with patch("mcpgen._bridge.httpx.AsyncClient", fake_client):
            await _bridge._pre_flight_refresh("acme", storage)

    return run


@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 408, 429])
def test_pre_flight_refresh_does_not_demand_a_new_login_when_the_endpoint_is_down(tmp_path, status_code):
    """A retryable status from the authorization server must not open the browser.

    The refresh token is untouched — what failed is the host that would renew it,
    and another browser round asks that same unreachable host for a token. Before
    this classification every non-200 became ReauthenticationRequired, so a batch
    produced one impossible interactive login per item.

    408 belongs here for the same reason as a 5xx even though it is a 4xx: RFC 6749
    §5.2 has the token endpoint report a dead grant as 400 (401 for client auth), so
    a 408 is the proxy in front of it saying the request never got processed.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, "<html>bad gateway</html>"))

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(run())

    assert not isinstance(excinfo.value, _bridge.ReauthenticationRequired)
    message = str(excinfo.value)
    assert str(status_code) in message
    assert "mcpgen login" not in message, "re-logging in cannot fix an unreachable endpoint"
    # The credential must survive: it is what the retry will use.
    assert json.loads(creds.read_text())["acme"]["tokens"]["refresh_token"] == "live_refresh"


@pytest.mark.parametrize("status_code", [403, 302, 404, 405])
def test_pre_flight_refresh_does_not_demand_a_new_login_for_a_block_page(tmp_path, status_code):
    """A WAF blocking the request is not the authorization server rejecting the grant.

    403 is the one that bites: a Cloudflare block, a bot-fight challenge, an IP
    denylist. Classifying it as a dead grant opens a browser that meets the very
    same block — the impossible re-prompt this whole change exists to remove. Same
    for a 3xx to a captive portal and a 404 from a moved endpoint: in none of them
    did a token request ever reach the authorization server.

    The type is what matters, not the wording: `ensure_login` branches on
    ReauthenticationRequired alone, so anything else keeps the browser shut. The
    message does name `mcpgen login` as a manual last resort, which is a suggestion
    the operator can weigh rather than a browser opening on its own.
    """
    creds = _refreshable_creds(tmp_path)
    blocked = "<html><body>Access denied (Cloudflare error 1020)</body></html>"
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, blocked))

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(run())

    assert not isinstance(excinfo.value, _bridge.ReauthenticationRequired)
    assert json.loads(creds.read_text())["acme"]["tokens"]["refresh_token"] == "live_refresh"


def test_pre_flight_refresh_believes_an_oauth_error_on_any_status(tmp_path):
    """Status alone does not decide it: the RFC error format identifies the speaker.

    A server that reports a revoked grant as 403 with a proper `error` body is
    non-compliant but real, and classifying it as retryable would strand the user —
    `ensure_login` would never offer the browser that is the actual fix. Nothing in
    front of an authorization server invents an `error` code, so the body is the
    thing worth trusting when the status disagrees.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(403, json_body={"error": "invalid_grant"}))

    with pytest.raises(_bridge.ReauthenticationRequired) as excinfo:
        asyncio.run(run())

    assert "mcpgen login acme" in str(excinfo.value)


@pytest.mark.parametrize(
    "status_code,error_code",
    [
        (400, "invalid_request"),  # malformed request — the same one login would resend
        (400, "unsupported_grant_type"),  # refresh_token not enabled on this client
        (400, "invalid_scope"),
    ],
)
def test_pre_flight_refresh_does_not_demand_a_new_login_for_a_faulted_request(tmp_path, status_code, error_code):
    """An `error` body is not by itself proof the grant died — the code says which it is.

    RFC 6749 §5.2 splits these: `invalid_grant` and `invalid_client` fault the
    credential, everything else faults the request. The first classification here read
    only the *shape* of the body, so a server answering `invalid_request` sent the user
    to a browser that produced a fresh token and then resent the identical malformed
    refresh — the re-prompt-per-item loop this whole change exists to remove, reached
    by a different door.

    The wording assertion is load-bearing. Every other branch also raises
    TokenRefreshUnavailable and also embeds the body, so a test that only checked the
    type and the echoed code would pass with this branch deleted outright — and the
    operator would then be told there was "no OAuth error body" when there was one.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, json_body={"error": error_code}))

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(run())

    message = str(excinfo.value)
    assert not isinstance(excinfo.value, _bridge.ReauthenticationRequired)
    assert "rejected the refresh request itself" in message, "this branch, not the no-error-body fallback"
    assert error_code in message, "the operator needs the code to act on it"
    # Diagnosis first, but never a dead end: `unauthorized_client` and codes from
    # outside the RFC can mean a registration a fresh login would replace.
    assert "mcpgen login acme" in message
    assert json.loads(creds.read_text())["acme"]["tokens"]["refresh_token"] == "live_refresh"


@pytest.mark.parametrize(
    "status_code,error_code",
    [
        (403, "temporarily_unavailable"),  # a status that is otherwise a block page
        (400, "server_error"),  # a status that would otherwise read as a rejection
    ],
)
def test_pre_flight_refresh_reads_a_retryable_error_code_on_any_status(tmp_path, status_code, error_code):
    """These codes name a passing condition, so the advice is retry — not reconfigure.

    Each pairs the code with a status that would classify differently on its own,
    which is what makes `_RETRYABLE_REFRESH_ERRORS` load-bearing: drop the set and
    these fall through to the configuration-problem branch, telling the operator to
    audit a client that is fine while the server is merely busy.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, json_body={"error": error_code}))

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(run())

    message = str(excinfo.value)
    assert "retry later" in message
    assert "configuration problem" not in message, "nothing here is misconfigured"
    assert json.loads(creds.read_text())["acme"]["tokens"]["refresh_token"] == "live_refresh"


@pytest.mark.parametrize(
    "body",
    [
        '["invalid_grant"]',  # a JSON array — .get() does not exist on a list
        '"invalid_grant"',  # a bare JSON string, same
        "123",
        '{"error": 123}',  # an object, but the code is not a string
        '{"error": {"code": "invalid_grant"}}',  # nested, not the §5.2 shape
        '{"error": null}',
    ],
)
def test_oauth_error_code_reads_only_the_rfc_shape(body):
    """A body that is JSON but not §5.2 must read as "no error code", not crash or match.

    Both guards in `_oauth_error_code` are load-bearing and neither is obvious. Valid
    JSON that is not an object makes `.get` an AttributeError, and a non-string `error`
    would otherwise flow into the frozenset lookups as an int or a dict. Dropping the
    `isinstance` check passes every other test in this file, so this is the only thing
    holding it.
    """

    class _Resp:
        text = body

        @staticmethod
        def json():
            return json.loads(body)

    assert _bridge._oauth_error_code(_Resp()) is None


_FORM_CT = "application/x-www-form-urlencoded"


@pytest.mark.parametrize(
    "content_type",
    [_FORM_CT, f"{_FORM_CT}; charset=UTF-8", f"  {_FORM_CT.upper()} ;charset=utf-8"],
    ids=["bare", "charset", "cased-and-padded"],
)
def test_pre_flight_refresh_classifies_a_form_encoded_dead_grant(tmp_path, content_type):
    """`Accept: application/json` is a request, not a guarantee — GitHub answers form-encoded.

    A server that ignores the header reports `invalid_grant` in a body the JSON parse
    cannot read, so before the fallback a genuinely revoked grant fell to the terminal
    "no OAuth error body" branch and raised TokenRefreshUnavailable forever. For a headless
    caller that is a permanent hard failure with no automatic route back — the outcome the
    dead-grant ordering exists to prevent.

    The media type is compared with parameters stripped and case folded, because
    `; charset=UTF-8` is what a real server sends and neither half of that is optional.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(
        400,
        "error=invalid_grant&error_description=token+revoked",
        headers={"Content-Type": content_type},
    )

    with pytest.raises(_bridge.ReauthenticationRequired) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    assert "mcpgen login acme" in str(excinfo.value)


def test_pre_flight_refresh_feeds_the_whole_cascade_from_a_form_encoded_body(tmp_path):
    """The fallback is not a dead-grant special case: every branch reads the same code.

    A `temporarily_unavailable` spelled form-encoded has to reach the retryable branch,
    not the terminal one — otherwise the fallback would have fixed the login prompt and
    left the "retry later" message reading like an unidentified proxy response.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(
        400,
        "error=temporarily_unavailable",
        headers={"Content-Type": _FORM_CT},
    )

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    message = str(excinfo.value)
    assert "retry later" in message
    assert "not how an authorization server reports a bad grant" not in message


def test_pre_flight_refresh_ignores_an_rfc_code_in_an_unlabelled_body(tmp_path):
    """The Content-Type gate is the whole difference between reading and scraping.

    A WAF block page containing the literal text `error=invalid_grant` is a body the
    authorization server did not send. Reading a code out of it manufactures exactly the
    speaker evidence the terminal branch reasons about *not* having, and the cost is one
    browser prompt — or one impossible `mcpgen login` demand — per batch item, for a
    credential that was never dead.

    The body has to be one `parse_qsl` can actually read, or this test proves nothing:
    `parse_qsl` splits on `&`, so an HTML page whose only `&` is absent yields a single
    pair whose key is the whole leading run and never `error`. Written that way it passes
    with the gate deleted. Written this way it fails — along with the `wrong-label` and
    `no-label` cases in `test_oauth_error_code_reads_a_labelled_form_body`.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(
        403,
        "<html><body>blocked</body></html>&error=invalid_grant",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    assert not isinstance(excinfo.value, _bridge.ReauthenticationRequired)
    assert "no OAuth error body" in str(excinfo.value)


@pytest.mark.parametrize(
    ("body", "content_type", "expected"),
    [
        ("error=invalid_grant", _FORM_CT, "invalid_grant"),
        ("error=invalid_grant&error=invalid_request", _FORM_CT, None),
        ("error=", _FORM_CT, None),
        ("error=invalid_grant", "text/plain", None),
        ("error=invalid_grant", "", None),
        ('{"error": "invalid_request"}', _FORM_CT, "invalid_request"),
        ("error=invalid_grant%0A", _FORM_CT, "invalid_grant"),
        ("error=%20%20", _FORM_CT, None),
        ("error=invalid_grant;error_description=x", _FORM_CT, "invalid_grant;error_description=x"),
    ],
    ids=["single", "duplicate", "empty", "wrong-label", "no-label", "json-wins", "padded", "blank", "semicolon"],
)
def test_oauth_error_code_reads_a_labelled_form_body(body, content_type, expected):
    """The form fallback, at the seam, including the three ways it must decline.

    Two `error` members mean a mangled or concatenated body; picking a winner would be a
    guess about which half the server sent, and `None` routes it to the branch that costs
    a message rather than a browser prompt. An empty value could not match either code set
    anyway, and "no OAuth error body" is the more honest of the two messages. `json-wins`
    pins the ordering: a body that parsed as JSON is the server's statement whatever its
    Content-Type claims, and re-reading it as a form would let a mislabelled response be
    classified twice, differently.

    `padded` is the one-byte regression: §5.2 values are NQSCHAR, so a trailing newline
    from a line-oriented intermediary is padding, and without the strip it turns a dead
    grant into an unrecognised code. `semicolon` pins the opposite decision — `;` has not
    been a form separator since Python 3.10 and is a legal byte inside a value, so the
    whole run stays the value and lands in the request-faulted branch. Accepting it would
    mean splitting on `;` too, which mis-reads compliant bodies to serve a server nobody
    has met.
    """

    class _Resp:
        text = body
        headers = _CaseInsensitiveHeaders({"content-type": content_type})

        @staticmethod
        def json():
            return json.loads(body)

    assert _bridge._oauth_error_code(_Resp()) == expected


def test_ensure_login_all_stops_at_the_first_failure_a_browser_cannot_fix(tmp_path):
    """The batch case the taxonomy exists for: abort, do not walk the whole list.

    `ensure_login_all` is a plain loop, so nothing catches LoginWontHelp on its behalf
    — which is the design. A caller that wrapped each server instead would be back to
    one impossible prompt per item.
    """
    creds = _refreshable_creds(tmp_path)

    async def run():
        with (
            patch("mcpgen._bridge.httpx.AsyncClient", _token_endpoint_replying(503, "unavailable")),
            patch("mcpgen._bridge.login", AsyncMock()) as fake_login,
        ):
            with pytest.raises(_bridge.LoginWontHelp):
                await _bridge.ensure_login_all(["acme", "beta", "gamma"], creds)
        fake_login.assert_not_called()

    asyncio.run(run())


def test_ensure_login_raises_when_the_login_leaves_no_credential(tmp_path):
    """A login that reports success and stores nothing must not be repeated.

    This is the loop the check exists for: `login()` returns, the store is still
    empty, so the next call finds no token and prompts again — forever. Checked on
    the call that prompted, while the before-state is still known, because a later
    call cannot tell "never stored" from an ordinary expiry.
    """
    creds = tmp_path / "credentials.json"

    async def run():
        with patch("mcpgen._bridge.login", AsyncMock()) as fake_login:  # writes nothing
            with pytest.raises(_bridge.LoginWontHelp) as excinfo:
                await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1  # one prompt, not one per iteration
        assert "acme" in str(excinfo.value)

    asyncio.run(run())


def test_ensure_login_raises_when_the_store_is_unchanged_by_the_login(tmp_path):
    """A store that silently discards the write reads the same as one never written.

    A backend that accepts the write without persisting it, or another process clearing
    the entry, leaves the prior entry in place. The token is gone but a *stale* one
    remains, so an emptiness check alone would miss it — the before/after comparison is
    what catches this one.
    """
    creds = _refreshable_creds(tmp_path)
    frozen = creds.read_text()

    async def restore_after_login(name, path=None, **kwargs):
        creds.write_text(frozen)  # whatever login wrote is reverted

    async def run():
        with (
            patch(
                "mcpgen._bridge.httpx.AsyncClient",
                _token_endpoint_replying(400, json_body={"error": "invalid_grant"}),
            ),
            patch("mcpgen._bridge.login", restore_after_login),
        ):
            with pytest.raises(_bridge.LoginWontHelp, match="no new credential"):
                await _bridge.ensure_login("acme", creds)

    asyncio.run(run())


def test_ensure_login_reports_a_nonsensical_lifetime_rather_than_crashing(tmp_path):
    """A lifetime RFC 6749 §5.1 forbids still has to come out as a message.

    `expires_in` is specified as a positive integer, so a negative one means a
    non-conforming endpoint rather than a condition with its own fix. It lands in the
    at-or-under-the-margin branch, which is the right verdict — the token is absent
    before it is written — and the message quotes what was reported, which is the only
    thing that tells the operator their server is the problem.
    """
    creds = tmp_path / "credentials.json"

    async def run():
        with patch("mcpgen._bridge.login", _login_that_writes(creds, expires_in=-3600)) as fake_login:
            with pytest.raises(_bridge.LoginWontHelp, match="lifetime of -3600s"):
                await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1

    asyncio.run(run())


def test_ensure_login_raises_when_the_lifetime_is_inside_the_margin(tmp_path):
    """A lifetime shorter than `_MARGIN` is unusable no matter what else is cached.

    `get_tokens` and `_pre_flight_refresh` both call a token absent from
    `expires_at - _MARGIN` onwards, so a token issued inside that window is gone from
    the moment it is written — and so is every renewal of it, which is why a refresh
    token (this fixture writes one) cannot rescue the case. Checking against bare
    `expires_at` would let exactly this loop through.
    """
    creds = tmp_path / "credentials.json"

    async def run():
        writer = _login_that_writes(creds, expires_in=_bridge._MARGIN // 2)
        with patch("mcpgen._bridge.login", writer) as fake_login:
            with pytest.raises(_bridge.LoginWontHelp, match=f"lifetime of {_bridge._MARGIN // 2}s"):
                await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1

    asyncio.run(run())


def test_ensure_login_accepts_a_short_lifetime_that_can_be_refreshed(tmp_path):
    """A lifetime past the margin plus the means to renew it is a working setup.

    Short access tokens paired with refresh tokens are a recommended hardening
    pattern, and the post-login check spends real time inside the lifetime it is
    verifying — `initialize()` and `list_tools()` run between the write and this read.
    Blocking on the margin alone would hard-fail such a server on the strength of how
    long that check happened to take. The next call's pre-flight renews out-of-band
    and the renewal clears the margin, so there is no loop to close here.
    """
    creds = tmp_path / "credentials.json"

    def login_writing_a_refreshable_token(name, path=None, **kwargs):
        creds.write_text(
            json.dumps(
                {
                    name: {
                        "tokens": {
                            "access_token": "tok1",
                            "refresh_token": "rt1",
                            "expires_in": _bridge._MARGIN + 60,
                            # The slack is already spent: the post-login check ran inside it.
                            "expires_at": int(time.time()) + _bridge._MARGIN - 1,
                        },
                        "client_info": {"client_id": "acme_id"},
                        "token_endpoint": "https://auth.example.com/token",
                    }
                }
            )
        )
        os.chmod(creds, 0o600)

    async def run():
        with patch("mcpgen._bridge.login", AsyncMock(side_effect=login_writing_a_refreshable_token)) as fake_login:
            await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1

    asyncio.run(run())


def test_ensure_login_raises_when_a_spent_lifetime_cannot_be_refreshed(tmp_path):
    """The same spent lifetime with nothing to renew it is the loop again.

    `_pre_flight_refresh` needs a refresh_token, a client_id and a token_endpoint.
    Missing any of them, the next call cannot renew and can only prompt — so this one
    has to be reported even though the endpoint's own lifetime was generous.
    """
    creds = tmp_path / "credentials.json"

    def login_writing_an_unrenewable_token(name, path=None, **kwargs):
        creds.write_text(
            json.dumps(
                {
                    name: {
                        "tokens": {
                            "access_token": "tok1",
                            "refresh_token": "rt1",
                            "expires_in": _bridge._MARGIN + 60,
                            "expires_at": int(time.time()) + _bridge._MARGIN - 1,
                        },
                        # no client_info, no token_endpoint: nothing to refresh with
                    }
                }
            )
        )
        os.chmod(creds, 0o600)

    async def run():
        with patch("mcpgen._bridge.login", AsyncMock(side_effect=login_writing_an_unrenewable_token)) as fake_login:
            with pytest.raises(_bridge.LoginWontHelp, match="Nothing cached can renew it"):
                await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1

    asyncio.run(run())


def test_ensure_login_accepts_a_token_with_no_expiry(tmp_path):
    """A token endpoint that omits `expires_in` leaves no `expires_at` to judge.

    `_serialize_tokens` writes the field only when a lifetime came back, and the
    freshness rule everywhere else treats its absence as "not expiring". The check
    has to agree, or a conformant server that omits the optional member cannot be
    logged into at all.
    """
    creds = tmp_path / "credentials.json"

    async def run():
        with patch("mcpgen._bridge.login", _login_that_writes(creds, expires_in=None)) as fake_login:
            await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1
        assert "expires_at" not in _bridge._stored_tokens(_bridge.FileTokenStorage("acme", creds))

    asyncio.run(run())


def _keyring_that_reads_but_will_not_write(store):
    """A keychain that answers reads, preloaded with *store*, and refuses writes.

    The read half is what these tests turn on: `_keyring_load` never fails over on it, so
    the instance stays on the keyring backend and reaches the split-store branch. The
    refusing `set_password` states the configuration being modelled — a macOS keychain item
    whose ACL permits reads and denies writes — but no test here drives a write; the
    fallback it would trigger is pinned separately by
    `test_keyring_backend_falls_back_to_file_when_unavailable`.
    """
    fake = _FakeKeyring()
    fake._store[(_bridge._KEYRING_SERVICE, _bridge._KEYRING_USER)] = json.dumps(store)

    def refuses(service, username, password):
        raise RuntimeError("access denied by keychain ACL")

    fake.set_password = refuses
    return fake


def test_verify_names_the_file_when_the_keychain_reads_but_will_not_write(tmp_path):
    """A store split across two backends is a dead credential, and must say so.

    `login()` builds its own storage, so a write-denied keychain flips *that* instance
    to the file and lands the token there, while this instance — and every later one,
    since `resolve_cred_backend` re-resolves and `_detect_keyring` never probes a write
    — goes on reading the keychain. The verdict is unchanged and right: nothing will
    ever read the new token, and another browser round repeats the split. The generic
    message would send the operator looking for a token that was never lost.
    """
    creds = tmp_path / "credentials.json"
    before = {"access_token": "old_tok"}
    started = time.time()
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "new_tok"}}}))
    os.chmod(creds, 0o600)
    # Stamped rather than left to the write: mtime granularity is 1s or worse on some
    # filesystems, which would truncate a during-login write to just before `started`.
    os.utime(creds, (started + 10, started + 10))
    fake_kr = _keyring_that_reads_but_will_not_write({"acme": {"tokens": before}})

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        storage = _bridge.FileTokenStorage("acme", creds, backend="keyring")
        with pytest.raises(_bridge.LoginWontHelp) as excinfo:
            _bridge._verify_login_took(storage, before, started)

    message = str(excinfo.value)
    assert "fell back to the file" in message
    assert str(creds) in message, "the operator has to be told where the token actually is"


def test_verify_ignores_a_file_that_predates_the_login(tmp_path):
    """`migrate-creds file keyring` keeps the source file, and that is not a fallback write.

    `purge` defaults to false, so a migrated user's `credentials.json` sits there holding
    whatever it held before — which differs from the keychain entry exactly as a fallback
    write would. Only the mtime tells them apart. Reporting a split store here would send
    the operator to a credential months out of date.
    """
    creds = tmp_path / "credentials.json"
    before = {"access_token": "old_tok"}
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "migrated_tok"}}}))
    os.chmod(creds, 0o600)
    fake_kr = _keyring_that_reads_but_will_not_write({"acme": {"tokens": before}})
    started = time.time()
    os.utime(creds, (started - 10, started - 10))  # last touched before the login began

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        storage = _bridge.FileTokenStorage("acme", creds, backend="keyring")
        with pytest.raises(_bridge.LoginWontHelp, match="holds no new credential") as excinfo:
            _bridge._verify_login_took(storage, before, started)

    assert "fell back to the file" not in str(excinfo.value)


def test_verify_falls_back_to_the_generic_message_when_the_file_has_nothing_either(tmp_path):
    """The split-store diagnosis is silent unless it has something to report.

    It reads a fallback chain it does not own. If `_keyring_save` ever stops failing
    over to the file, this branch must cost wording rather than a verdict — so a file
    holding nothing fresh falls straight through to the generic message.

    The file is present and recent here on purpose: absent, the mtime gate answers first
    and this pins the unreadable-file exit instead of the one it is named for.
    """
    creds = tmp_path / "credentials.json"
    before = {"access_token": "old_tok"}
    creds.write_text(json.dumps({"acme": {"tokens": before}}))
    os.chmod(creds, 0o600)
    fake_kr = _keyring_that_reads_but_will_not_write({"acme": {"tokens": before}})
    started = time.time()
    os.utime(creds, (started + 10, started + 10))

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        storage = _bridge.FileTokenStorage("acme", creds, backend="keyring")
        with pytest.raises(_bridge.LoginWontHelp, match="holds no new credential") as excinfo:
            _bridge._verify_login_took(storage, before, started)

    assert "fell back to the file" not in str(excinfo.value)


def test_verify_falls_back_to_the_generic_message_when_the_file_is_unreadable(tmp_path):
    """An unreadable file is not a diagnosis — the split-store branch must stay silent.

    The keyring backend need never have written the file at all, so `stat` raising is
    the ordinary case rather than an error worth reporting. It has to reach the generic
    verdict, not a traceback.
    """
    creds = tmp_path / "credentials.json"  # never created
    before = {"access_token": "old_tok"}
    fake_kr = _keyring_that_reads_but_will_not_write({"acme": {"tokens": before}})

    with patch.dict("sys.modules", {"keyring": fake_kr}):
        storage = _bridge.FileTokenStorage("acme", creds, backend="keyring")
        with pytest.raises(_bridge.LoginWontHelp, match="holds no new credential") as excinfo:
            _bridge._verify_login_took(storage, before, time.time())

    assert "fell back to the file" not in str(excinfo.value)


def test_verify_does_not_claim_permanence_when_its_own_keychain_read_failed(tmp_path):
    """A check that lost the keychain mid-call cannot speak for what login wrote.

    `_warn_keyring_fallback` flips this instance to the file for the rest of its life, but
    `login()` resolved the backend afresh and may have written the keychain successfully —
    which this instance can no longer see. The verdict stands, since nothing here can read
    the credential, but "logging in again would repeat a round that already succeeded"
    would be a false claim of permanence: a retry in a new process re-resolves to keyring.
    """
    creds = tmp_path / "credentials.json"
    before = {"access_token": "old_tok"}

    class _KeyringThatFailsReads:
        def get_password(self, service, username):
            raise RuntimeError("keychain is locked")

        def set_password(self, service, username, password):
            raise RuntimeError("keychain is locked")

    with patch.dict("sys.modules", {"keyring": _KeyringThatFailsReads()}):
        storage = _bridge.FileTokenStorage("acme", creds, backend="keyring")
        with pytest.warns(UserWarning, match="keyring unusable"):
            with pytest.raises(_bridge.LoginWontHelp, match="holds no new credential") as excinfo:
                _bridge._verify_login_took(storage, before, time.time())

    message = str(excinfo.value)
    assert "unusable during this call" in message
    assert "repeat a round that already succeeded" not in message, "the check cannot know that here"
    assert storage._lock_backend == "keyring" and storage._backend == "file", "the flip is what it detected"


def test_verify_says_nothing_about_a_keychain_on_the_file_backend(tmp_path):
    """The split-store branch is keyring-only; a file-backend store names the file."""
    creds = tmp_path / "credentials.json"
    before = {"access_token": "old_tok"}
    creds.write_text(json.dumps({"acme": {"tokens": before}}))
    os.chmod(creds, 0o600)

    storage = _bridge.FileTokenStorage("acme", creds, backend="file")
    with pytest.raises(_bridge.LoginWontHelp, match="holds no new credential") as excinfo:
        _bridge._verify_login_took(storage, before, time.time())

    message = str(excinfo.value)
    assert "keychain" not in message
    assert str(creds) in message


def test_a_sub_margin_refresh_response_is_stale_the_moment_it_is_stored(tmp_path):
    """A refresh lands its own lifetime, which the margin can swallow whole.

    The post-login check never sees this: the store right after a login holds the
    *exchange* token, and what a later refresh response will report is not in it. So a
    server whose exchange lifetimes clear the margin and whose refresh lifetimes do not
    renews into a token every reader calls absent, and `ensure_login` reads that as
    first-time and prompts — once per refresh interval, and accepted as such: the
    margin-dead write is the pre-flight's, made between logins, so no post-login check can
    reach it, and judging it after the pre-flight's own store would misfire on a server
    legitimately answering with a short remainder near a fixed absolute expiry. Pinned as
    the mechanism, which is what that trade rests on rather than any verdict here.
    """
    creds = _refreshable_creds(tmp_path)
    short = {"access_token": "renewed", "token_type": "Bearer", "expires_in": _bridge._MARGIN // 2}

    async def run():
        storage = _bridge.FileTokenStorage("acme", creds)
        with patch("mcpgen._bridge.httpx.AsyncClient", _token_endpoint_replying(200, json_body=short)):
            await _bridge._pre_flight_refresh("acme", storage)
        assert _bridge._stored_tokens(storage)["access_token"] == "renewed", "the refresh did land"
        assert await storage.get_tokens() is None, "and every reader calls it absent anyway"

    asyncio.run(run())


def test_ensure_login_accepts_a_login_that_stores_a_usable_token(tmp_path):
    """The healthy path stays silent — the check must not fire on a normal login."""
    creds = tmp_path / "credentials.json"

    async def run():
        with patch("mcpgen._bridge.login", _login_that_writes(creds)) as fake_login:
            await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1
        assert _bridge._stored_tokens(_bridge.FileTokenStorage("acme", creds))["access_token"] == "tok1"

    asyncio.run(run())


def test_ensure_login_can_log_in_again_after_a_later_expiry(tmp_path):
    """A process outliving its own grant must still be able to log in again.

    The reason this check is not remembered across calls: a revoked grant and a login
    that never took present identically later on — a token is present and the server
    refuses it. A process that logs in, runs for a while and then has its grant die
    has to get the browser round it genuinely needs.
    """
    creds = tmp_path / "credentials.json"

    async def run():
        with patch("mcpgen._bridge.login", _login_that_writes(creds)) as fake_login:
            await _bridge.ensure_login("acme", creds)
            # Time passes: the token expires and the grant behind it is revoked.
            stored = json.loads(creds.read_text())
            stored["acme"]["tokens"]["expires_at"] = 1
            stored["acme"]["client_info"] = {"client_id": "acme_id"}
            stored["acme"]["token_endpoint"] = "https://auth.example.com/token"
            creds.write_text(json.dumps(stored))
            with patch(
                "mcpgen._bridge.httpx.AsyncClient",
                _token_endpoint_replying(400, json_body={"error": "invalid_grant"}),
            ):
                await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 2

    asyncio.run(run())


def test_ensure_login_all_logs_in_every_server(tmp_path):
    """A first run of N servers gets N logins — the check is per call, not a budget."""
    creds = tmp_path / "credentials.json"

    async def run():
        with patch("mcpgen._bridge.login", _login_that_writes(creds)) as fake_login:
            await _bridge.ensure_login_all(["acme", "beta", "gamma"], creds)
        assert fake_login.call_count == 3

    asyncio.run(run())


def test_ensure_login_propagates_a_transient_login_failure_unchanged(tmp_path):
    """A cancelled or timed-out login is not the store's fault and keeps its own type.

    `login()` re-raises a cancelled consent screen, a callback timeout and an unpasted
    URL unchanged, and every one of those messages invites a retry. The post-login
    check runs only when `login()` returned, so it neither reclassifies these as
    LoginWontHelp nor stands in the way of the retry.
    """
    creds = tmp_path / "credentials.json"
    writer = _login_that_writes(creds)
    attempts = []

    async def flaky_login(name, path=None, **kwargs):
        attempts.append(name)
        if len(attempts) == 1:
            raise TimeoutError("No OAuth callback received within 300s.")
        await writer(name, path, **kwargs)

    async def run():
        with patch("mcpgen._bridge.login", flaky_login):
            with pytest.raises(TimeoutError):
                await _bridge.ensure_login("acme", creds)
            await _bridge.ensure_login("acme", creds)  # the retry the message invites
        assert attempts == ["acme", "acme"]

    asyncio.run(run())


class _SessionThatWorks:
    """`ClientSession` stand-in whose `initialize()` and `list_tools()` both succeed.

    The only stand-in in this file that lets `login()` run to completion. Every other
    login test fails the session deliberately, which stops short of the post-token
    stretch where a hoisted post-login check would sit.
    """

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[])


@asynccontextmanager
async def _fake_http_ok(*args, **kwargs):
    yield (None, None, None)


def test_login_itself_does_not_run_the_post_login_check(tmp_path):
    """`mcpgen login` reports what happened; it does not police the store.

    The check belongs to `ensure_login`, which is the automatic path and the one that
    can loop. Hoisting it into `login()` — the obvious simplification, since that is
    where the browser opens — would make an explicit `mcpgen login` fail on a store it
    was asked to write and had no say over.

    The provider here writes no token, which is exactly the condition `_verify_login_took`
    raises on, and the session runs all the way through `list_tools()`, so the whole
    post-token stretch a hoisted check would live in is executed. `login()` must still
    return cleanly. A test that stops earlier — at server resolution, say — passes
    whether the check is there or not.
    """
    creds = tmp_path / "credentials.json"

    def provider_that_writes_no_token(**kwargs):
        return SimpleNamespace(
            context=SimpleNamespace(oauth_metadata=None),
            _get_token_endpoint=lambda: "https://auth.example.com/token",
        )

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", _fake_http_ok),
            patch("mcpgen._bridge.ClientSession", _SessionThatWorks),
            patch("mcpgen._bridge.OAuthClientProvider", provider_that_writes_no_token),
        ):
            await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())
    assert _bridge._stored_tokens(_bridge.FileTokenStorage("acme", creds)) == {}, (
        "the premise: the store holds no token, which is what ensure_login would reject"
    )


@pytest.mark.parametrize("error_code", ["invalid_grant", "invalid_client", "unauthorized_client"])
def test_ensure_login_re_registers_when_the_registration_is_what_failed(tmp_path, error_code):
    """A condition re-registration can repair must not need a human to notice it.

    `unauthorized_client` is the one that argues for itself. Filing it as
    TokenRefreshUnavailable reads well interactively — the message explains the likely
    misconfiguration — but automation and headless callers never see a prompt, so a
    condition `login()` fixes on its own becomes a permanent failure until a person
    runs the printed command. A server whose policy genuinely forbids refresh for this
    client answers the same way to the new registration, costing one prompt per expiry;
    that is the cheaper of the two errors.
    """
    creds = _refreshable_creds(tmp_path)

    async def run():
        with (
            patch("mcpgen._bridge.httpx.AsyncClient", _token_endpoint_replying(400, json_body={"error": error_code})),
            patch("mcpgen._bridge.login", _login_that_writes(creds)) as fake_login,
        ):
            await _bridge.ensure_login("acme", creds)
        assert fake_login.call_count == 1

    asyncio.run(run())


def test_pre_flight_refresh_passes_retry_after_through(tmp_path):
    """ "Retry later" is not actionable without a when, and the server often sends one.

    The header name is checked case-insensitively, as httpx does: servers send
    `Retry-After`, and reading a plain dict for `retry-after` would drop it silently.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(429, "slow down", headers={"Retry-After": "120"})

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    assert "Retry-After: 120" in str(excinfo.value)


def test_pre_flight_refresh_says_nothing_about_retry_after_when_none_is_sent(tmp_path):
    """No header must not render an empty or `None` value into the operator's line."""
    creds = _refreshable_creds(tmp_path)

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(503, "unavailable"))())

    assert "Retry-After" not in str(excinfo.value)
    assert "None" not in str(excinfo.value)


def test_pre_flight_refresh_takes_a_200_that_carries_a_token(tmp_path):
    """A good token response is not disqualified by a stray `error` member.

    Some servers pad every response with `"error": ""`. Reading the error code before
    the token would fail a refresh that plainly succeeded, and `ensure_login` would
    then open a browser for a credential that had just been renewed.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(200, json_body={"access_token": "renewed_tok", "token_type": "bearer", "error": ""})

    asyncio.run(_run_pre_flight(creds, fake)())

    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "renewed_tok"


def test_pre_flight_refresh_demands_a_new_login_for_a_dead_grant_reported_with_200(tmp_path):
    """Failure reported in-band on a 200 still reaches the browser when the grant died.

    Slack's token-rotation endpoint answers `{"ok": false, "error": ...}` with a 200.
    Deciding on the status alone would file a revoked refresh token as an unparseable
    body — retryable forever, and `ensure_login` never offers the fix.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(200, json_body={"ok": False, "error": "invalid_grant"})

    with pytest.raises(_bridge.ReauthenticationRequired) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    assert "mcpgen login acme" in str(excinfo.value)


def test_ensure_login_does_not_open_the_browser_for_a_faulted_request(tmp_path):
    """The end-to-end half of the case above: no browser, through the real entry point."""
    creds = _refreshable_creds(tmp_path)

    async def run():
        with (
            patch(
                "mcpgen._bridge.httpx.AsyncClient",
                _token_endpoint_replying(400, json_body={"error": "invalid_request"}),
            ),
            patch("mcpgen._bridge.login", AsyncMock()) as fake_login,
        ):
            with pytest.raises(_bridge.TokenRefreshUnavailable):
                await _bridge.ensure_login("acme", creds)
        fake_login.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("status_code", [400, 401])
def test_pre_flight_refresh_does_not_demand_a_new_login_for_a_bare_rejection(tmp_path, status_code):
    """A 400/401 with no OAuth error body did not come from the authorization server.

    §5.2 requires the JSON `error` body on exactly these two statuses, so a bare or
    HTML one is an auth proxy, a WAF, or a gateway — none of which a browser round
    reaches. This used to take the status at face value on the theory that a terse
    non-compliant server might mean a dead grant; that theory cost a guaranteed
    useless browser prompt on every proxy 400 to buy an auto-prompt for a server that
    violates the spec. The message carries `mcpgen login` instead, so the terse-server
    case still has a way out — a printed command rather than a browser nobody asked for.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, "<html>Forbidden</html>"))

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(run())

    assert not isinstance(excinfo.value, _bridge.ReauthenticationRequired)
    assert "mcpgen login acme" in str(excinfo.value), "the manual fallback is the only way out here"


def test_pre_flight_refresh_classifies_a_200_that_is_not_a_token(tmp_path):
    """An interstitial served with 200 must not escape as a raw parse error.

    It used to reach `OAuthToken(**resp.json())` and raise JSONDecodeError or a
    pydantic ValidationError, which no caller catches: `mcpgen login` printed a
    traceback and batch callers could not branch on it at all.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(200, "<html>sign in to continue</html>"))

    with pytest.raises(_bridge.TokenRefreshUnavailable):
        asyncio.run(run())

    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "stale_tok"


@pytest.mark.parametrize(
    "status_code,body",
    [
        (400, '{"error":"invalid_grant"}'),
        (401, '{"error":"invalid_client"}'),
        (400, '{"error":"unauthorized_client"}'),
        # The description is free text and irrelevant to the decision.
        (400, '{"error":"invalid_grant","error_description":"Token is not active"}'),
    ],
)
def test_pre_flight_refresh_demands_a_new_login_when_the_grant_is_dead(tmp_path, status_code, body):
    """The OAuth error codes a browser round *does* fix.

    `invalid_grant` is the refresh token itself. `invalid_client` and
    `unauthorized_client` are the registration, which `login()` replaces by dropping
    the cached `client_info` and re-running dynamic client registration. Narrowing the
    dead-grant test to the error code must not swallow these — `ensure_login` opens
    the browser for this type and no other, so misfiling them leaves an expired
    credential with no automatic way back.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, body))

    with pytest.raises(_bridge.ReauthenticationRequired) as excinfo:
        asyncio.run(run())

    assert "mcpgen login acme" in str(excinfo.value)


def test_pre_flight_refresh_classifies_a_transport_error(tmp_path):
    """A connect error used to escape unclassified, so callers could not branch on it."""
    creds = _refreshable_creds(tmp_path)
    original = httpx.ConnectError("[Errno 8] nodename nor servname provided")

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data, headers=None):
            raise original

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _FakeClient)())

    assert excinfo.value.__cause__ is original
    assert "ConnectError" in str(excinfo.value), "the operator has to see what went wrong"


def test_pre_flight_refresh_classifies_an_unusable_token_endpoint(tmp_path):
    """Not every httpx failure is an httpx.HTTPError, and the Raises contract is total.

    `InvalidURL` and `CookieConflict` derive from Exception directly, so `except
    httpx.HTTPError` let them escape as neither of the two documented types —
    unclassified, which is the exact failure this classification exists to remove.
    `credentials.json` is hand-editable, so a token_endpoint that is not a usable URL
    is a reachable state and not a hypothetical.
    """
    creds = _refreshable_creds(tmp_path, token_endpoint="not a url")
    original = httpx.InvalidURL("Invalid URL component 'scheme'")
    assert not isinstance(original, httpx.HTTPError), "the premise of this test"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data, headers=None):
            raise original

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _FakeClient)())

    assert excinfo.value.__cause__ is original
    assert not isinstance(excinfo.value, _bridge.ReauthenticationRequired)


@pytest.mark.parametrize("status_code", [503, 429])
def test_pre_flight_refresh_reads_the_grant_before_the_retryable_status(tmp_path, status_code):
    """When the status says "retry" and the body says "dead grant", the body wins.

    The two conditions overlap — a server can shed load with a 503 and still name
    `invalid_grant`, and a rate limiter can sit in front of a real rejection. The
    branch order resolves it deliberately, and it must resolve it this way: a proxy
    does not invent an RFC error code, so the server did speak. Getting it wrong in
    this direction costs one unnecessary browser prompt; getting it wrong the other
    way files a genuine revocation as "retry later", and ensure_login then never
    offers the browser at all — no route back without a human reading the message.

    Pinned because the ordering is otherwise invisible to the suite: reversing the
    two blocks reads like a harmless cleanup, and every other test still passes.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(status_code, json_body={"error": "invalid_grant"}))

    with pytest.raises(_bridge.ReauthenticationRequired) as excinfo:
        asyncio.run(run())

    assert "mcpgen login acme" in str(excinfo.value)


def test_pre_flight_refresh_does_not_print_the_token_it_could_not_parse(tmp_path):
    """A 200 that fails validation is still a token response — it must not reach a log.

    This is the dangerous shape precisely because it looks harmless: a `token_type`
    outside the `Bearer` literal, or a non-integer `expires_in`, fails OAuthToken and
    lands on the error path with a live access_token and refresh_token in the body.
    Both cli.py and the generated runner print that message to stderr, so an echoed
    body is a credential in CI logs. pydantic v2 also embeds the rejected input_value
    in its own message, which is why the exception type is named instead of described.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(
        200,
        json_body={
            "access_token": "SECRET_ACCESS",
            "refresh_token": "SECRET_REFRESH",
            "id_token": "SECRET_ID",
            "token_type": "not-a-bearer",
            "error_description": "issued but malformed",
        },
    )

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    message = str(excinfo.value)
    for secret in ("SECRET_ACCESS", "SECRET_REFRESH", "SECRET_ID"):
        assert secret not in message, f"{secret} leaked into an error printed to stderr"
    assert "<redacted>" in message
    # Redaction is not censorship: what makes the message worth printing has to survive.
    assert "issued but malformed" in message
    assert "mcpgen login acme" in message


def test_pre_flight_refresh_does_not_let_the_validation_error_carry_the_body(tmp_path):
    """Redacting the body is not enough while pydantic quotes the body back at us.

    Verified against pydantic 2.13: a *field-level* failure quotes only that field, but
    a `missing` error quotes the whole input — `input_value={'refresh_token': '...'}`.
    A response that rotates the refresh token but omits `access_token` produces exactly
    that, so describing the exception re-prints the credential the excerpt just scrubbed.
    Naming the exception type instead is what closes it; this pins that choice.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(200, json_body={"refresh_token": "SECRET_REFRESH"})

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    assert "SECRET_REFRESH" not in str(excinfo.value)
    assert "ValidationError" in str(excinfo.value), "the operator still has to see what failed"


@pytest.mark.parametrize(
    ("json_body", "expected"),
    [
        ({"refresh_token": "SECRET1"}, _bridge.TokenRefreshUnavailable),
        # `token_type` outside the `Bearer` literal is what fails validation here — the
        # body has to both name a dead grant and still carry a credential, which is the
        # padded shape `_body_excerpt` exists for.
        (
            {"error": "invalid_grant", "access_token": "SECRET1", "token_type": "bogus"},
            _bridge.ReauthenticationRequired,
        ),
    ],
    ids=["not-a-token", "dead-grant-with-token"],
)
def test_pre_flight_refresh_does_not_chain_the_body_into_a_traceback(tmp_path, json_body, expected):
    """A clean message on an exception that chains the body is a leak that moved, not closed.

    The chain travels with the exception object, and every CLI command except `login`
    lets these types reach the interpreter — which prints the whole chain to stderr, i.e.
    into the CI logs this redaction exists for. `format_exception` renders it exactly as
    the interpreter would at exit, so this is the direct proof and not a proxy for one.

    The short secret is the point: pydantic truncates the `input_value` repr, so a long
    token leaks a prefix and a short one leaks whole. Asserting on a long token would
    pass with the chain restored.

    `__suppress_context__` is pinned alongside the rendering because a regression to a
    bare `raise` still displays the context while `str()` stays clean — a rendering-only
    assertion could miss it if the fake ever changes.
    """
    creds = _refreshable_creds(tmp_path)

    with pytest.raises(expected) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, json_body=json_body))())

    exc = excinfo.value
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert "SECRET1" not in rendered
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_pre_flight_refresh_redacts_a_form_encoded_token_body(tmp_path):
    """The JSON path is not the only one that can carry a credential.

    GitHub's token endpoint answers form-encoded unless asked otherwise, and a body
    that does not parse as JSON skips the member-wise redaction entirely. Without the
    regex fallback the same secret leaks through a differently-encoded door.
    """
    creds = _refreshable_creds(tmp_path)
    body = "access_token=SECRET_ACCESS&refresh_token=SECRET_REFRESH&scope=repo&token_type=bearer"

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    message = str(excinfo.value)
    assert "SECRET_ACCESS" not in message
    assert "SECRET_REFRESH" not in message
    assert "scope=repo" in message, "the non-secret members are the diagnostic"


def test_pre_flight_refresh_redacts_a_nested_token_body(tmp_path):
    """A credential one level down is still a credential.

    Slack answers `{"ok": …, "authed_user": {"access_token": …}}` — the very endpoint
    `_pre_flight_refresh` singles out for in-band failure handling. A top-level scan of
    the parsed members finds no secret there and prints the live token verbatim, so the
    redaction has to walk the whole structure rather than the outermost mapping.

    The value is a *list* deliberately. A nested string value is covered by the regex
    fallback too, so with a string here the test would still pass on a walk flattened to
    one level — it would pin nothing. Only the structural walk reaches a non-string value.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(
        200,
        json_body={
            "ok": False,
            "error": "token_revoked",
            "authed_user": {"access_token": ["SECRET_ACCESS"], "scope": "chat:write"},
        },
    )

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    message = str(excinfo.value)
    assert "SECRET_ACCESS" not in message, "a nested token leaked into an error printed to stderr"
    assert "<redacted>" in message
    assert "chat:write" in message, "the non-secret members are the diagnostic"


def test_pre_flight_refresh_redacts_a_truncated_json_token_body(tmp_path):
    """A body cut short by a proxy fails `resp.json()` while still carrying the token.

    The structured pass cannot reach it — that is exactly the case the parse failed on —
    and the form-encoded regex does not match JSON syntax. Without a JSON-shaped fallback
    running unconditionally, the least-parseable body is the one that leaks.
    """
    creds = _refreshable_creds(tmp_path)
    body = '{"ok": false, "authed_user": {"access_token": "SECRET_ACCESS", "scope": "chat'

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    assert "SECRET_ACCESS" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_pre_flight_refresh_redacts_a_body_cut_inside_the_token(tmp_path):
    """The most-truncated body must not be the one that leaks.

    A cut *after* the token still leaves its closing quote, so a value pattern that
    insists on one passes that case while failing the shape the fallback exists for:
    the proxy that stopped mid-token. The value therefore also ends at end-of-text.
    """
    creds = _refreshable_creds(tmp_path)
    body = '{"ok": false, "authed_user": {"access_token": "SECRET_ACCESS_TAIL'

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    assert "SECRET_ACCESS_TAIL" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_pre_flight_refresh_redacts_past_an_escaped_quote_in_the_token(tmp_path):
    """Stopping at the first `"` inside the value would re-emit everything after it.

    A value pattern that cannot step over `\\"` ends the match early, so the redaction
    covers the head of the token and the substitution prints the tail back out.
    """
    creds = _refreshable_creds(tmp_path)
    body = '{"access_token": "SEC\\"RET_TAIL", "scope": "chat'

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    assert "RET_TAIL" not in str(excinfo.value)


def test_pre_flight_refresh_asks_the_token_endpoint_for_json(tmp_path):
    """Without Accept: application/json some servers never send an OAuth error body.

    The whole classification reads that body to tell the authorization server apart
    from whatever stands in front of it. A server defaulting to form-encoded — GitHub
    again — would present every rejection, `invalid_grant` included, as a body with no
    error code, i.e. as a block page, and a genuinely dead grant would never prompt.
    """
    creds = _refreshable_creds(tmp_path)
    record = {}
    fake = _token_endpoint_replying(200, json_body={"access_token": "tok", "token_type": "bearer"}, record=record)

    asyncio.run(_run_pre_flight(creds, fake)())

    assert record["headers"]["Accept"] == "application/json"


def test_pre_flight_refresh_marks_a_truncated_body_as_truncated(tmp_path):
    """An operator must be able to tell a short body from the front of a huge one.

    `_describe` appends an ellipsis when it truncates; the response-body excerpt used
    not to, so a 20KB block page and a 200-character one read identically.
    """
    creds = _refreshable_creds(tmp_path)
    run = _run_pre_flight(creds, _token_endpoint_replying(503, "<html>" + "x" * 5000 + "</html>"))

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(run())

    assert "…" in str(excinfo.value)


def test_ensure_login_does_not_open_the_browser_when_the_token_endpoint_is_down(tmp_path):
    """The regression this classification exists for: no browser prompt on an outage.

    ensure_login() catches ReauthenticationRequired and only that, so the fix is
    load-bearing here — misclassify the 503 and login() runs, opening a browser the
    outage guarantees cannot help.
    """
    creds = _refreshable_creds(tmp_path)

    async def run():
        with (
            patch("mcpgen._bridge.httpx.AsyncClient", _token_endpoint_replying(503, "unavailable")),
            patch("mcpgen._bridge.login", AsyncMock()) as fake_login,
        ):
            with pytest.raises(_bridge.TokenRefreshUnavailable):
                await _bridge.ensure_login("acme", creds)
        fake_login.assert_not_called()

    asyncio.run(run())


def test_pre_flight_refresh_still_renews_on_200(tmp_path):
    """The happy path stays untouched by the classification above."""
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(200, json_body={"access_token": "renewed_tok", "token_type": "bearer"})

    asyncio.run(_run_pre_flight(creds, fake)())

    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "renewed_tok"


def test_the_failures_a_browser_cannot_fix_share_one_catchable_type(tmp_path):
    """Batch callers catch LoginWontHelp once instead of tracking a growing list.

    ReauthenticationRequired must stay outside it: there the browser *is* the fix,
    and folding it in would make one except clause swallow both answers.
    """
    assert issubclass(_bridge.PostLoginCheckFailed, _bridge.LoginWontHelp)
    assert issubclass(_bridge.TokenRefreshUnavailable, _bridge.LoginWontHelp)
    assert not issubclass(_bridge.ReauthenticationRequired, _bridge.LoginWontHelp)


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


def test_describe_caps_a_runaway_exception_group():
    """Leaves are bounded but their number is not — the join has to be capped too.

    Without this, a group of N fat leaves produces an N × ~215-character "one-line"
    message, which is the same unreadable CLI line the per-leaf cap exists to stop.
    """
    group = ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("x" * 5000) for _ in range(20)])

    described = _bridge._describe(group)

    assert len(described) < 800
    assert described.endswith("…")
    assert described.startswith("RuntimeError: xxx")


def test_login_survives_an_unreadable_credential_store(tmp_path):
    """A corrupt store must not replace the failure the operator actually needs to see.

    The re-read lives inside `except BaseException`, so a JSONDecodeError from it
    used to propagate in place of the transport error that got us there — and the
    restore would have written the stash on top of an unreadable file.

    The store is readable at the start (that read stashes the previous entry) and
    breaks during the flow: a keyring backend that starts failing, or a file another
    process truncated. A store that is already unreadable never reaches the handler.

    A previous credential exists, so the restore branch is live — writing the stash
    back on top of a store that could not be read is the one move that could turn a
    server outage into a lost credential.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "old_tok"}}}))
    os.chmod(creds, 0o600)
    original = RuntimeError("502 Bad Gateway")
    run = _run_login_failing_after_exchange(creds, original, {"access_token": "fresh_tok"})

    real_load = _bridge.FileTokenStorage._load

    def breaks_once_the_new_token_is_written(self):
        # Keyed on state, not call count: everything up to the token exchange must
        # read normally, and only the handler's re-read sees the broken store.
        data = real_load(self)
        if data.get("acme", {}).get("tokens", {}).get("access_token") == "fresh_tok":
            raise json.JSONDecodeError("Expecting value", "", 0)
        return data

    with (
        patch.object(_bridge.FileTokenStorage, "_load", breaks_once_the_new_token_is_written),
        pytest.raises(RuntimeError) as excinfo,
    ):
        asyncio.run(run())

    on_disk = json.loads(creds.read_text())
    assert on_disk["acme"]["tokens"]["access_token"] == "fresh_tok", (
        "the stash must not be restored over a store that could not be read"
    )

    assert excinfo.value is original
    assert not isinstance(excinfo.value, json.JSONDecodeError)


def test_login_quarantines_a_store_it_cannot_parse(tmp_path, capsys):
    """`mcpgen login` is the recovery command, so it cannot be the one a corrupt store kills.

    The 0.7.0 guard covers the handler's *re-read*. The read at the top of `login()` still
    ran bare, so a truncated `credentials.json` met the one command whose job is writing a
    fresh entry with a raw JSONDecodeError and no route back but deleting the file by hand.

    Falling through to `{}` alone would be worse than the traceback: the `_save` on the next
    line writes that empty view over the file, taking every other server's entry with it.
    The bad bytes have to survive somewhere, which is what quarantine buys.
    """
    creds = tmp_path / "credentials.json"
    garbage = '{"acme": {"tokens": {"access_'  # truncated mid-write
    creds.write_text(garbage)
    os.chmod(creds, 0o600)

    @asynccontextmanager
    async def fake_http_fail(*args, **kwargs):
        raise RuntimeError("network error")
        yield  # makes this an async generator; unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())

    quarantined = list(tmp_path.glob("credentials.json.corrupt.*"))
    assert len(quarantined) == 1, "the unreadable bytes are the only copy of the other entries"
    assert quarantined[0].read_text() == garbage
    # The quarantined file still holds every other server's live tokens, so it is a
    # credential store under a different name. `os.replace` preserves the mode, which is
    # why this passes — and why nothing would notice if the quarantine ever grew a copy
    # step that did not.
    assert stat.S_IMODE(quarantined[0].stat().st_mode) == 0o600
    assert "moved to" in capsys.readouterr().err, "silent quarantine is a file the user never finds"
    # login() clears the entry before the flow and the flow here fails before any token
    # exchange, so the live store must exist and be readable — and must not carry a
    # fabricated entry for a login that never produced one.
    assert json.loads(creds.read_text()) == {}


@pytest.mark.parametrize(
    "body",
    [
        '{"Access_Token": "s3cr3t", "scope": "repo"}',
        '{"authed_user": {"REFRESH_TOKEN": "s3cr3t"}, "scope": "repo"}',
        "ACCESS_TOKEN=s3cr3t&scope=repo",
        '{"Id_Token": "s3cr3t", "scope": "repo',
        '{"accessToken": "s3cr3t", "scope": "repo"}',
        '{"access-token": "s3cr3t", "scope": "repo"}',
        '{"authed_user": {"refreshToken": "s3cr3t"}, "scope": "repo"}',
        '{"clientSecret": "s3cr3t", "scope": "repo',
        "accessToken=s3cr3t&scope=repo",
        '{"access-token": "s3cr3t", "scope": "repo',
        "access-token=s3cr3t&scope=repo",
    ],
    ids=[
        "json",
        "nested",
        "form",
        "truncated",
        "camel",
        "kebab",
        "nested-camel",
        "truncated-camel",
        "form-camel",
        "truncated-kebab",
        "form-kebab",
    ],
)
def test_body_excerpt_matches_secret_members_case_insensitively(tmp_path, body):
    """§5.1 mandates the lowercase spelling, but it binds the authorization server.

    Every body that reaches this function is one where something else may have answered —
    a WAF, a gateway, a vendor wrapper with its own naming convention. Holding a
    non-compliant responder to the compliant spelling is how the credential gets printed,
    and the single most common thing such a wrapper does to a member name is re-case it: a
    JSON serializer on its defaults emits `accessToken`. The camel and kebab cases run on
    the truncated and form bodies too, because those take the regex path rather than the
    structured one — a normalisation applied to only one of the two leaves the other a
    generation behind, which is a leak that no test of the parsed shape would catch.
    """
    creds = _refreshable_creds(tmp_path)

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    message = str(excinfo.value)
    assert "s3cr3t" not in message
    assert "<redacted>" in message
    assert "repo" in message, "folding the case must not start eating non-secret members"


def test_body_excerpt_redacts_a_secret_nested_inside_a_json_string(tmp_path):
    """The structured pass matches keys, so a token inside a *string* has no key to find.

    A gateway echoing an upstream body into `error_description` produces exactly that, and
    the body parses cleanly — so `scrubbed == parsed`, nothing is re-serialised, and the
    regex fallback is the only thing standing between the token and stderr. This is the
    case that makes the fallback load-bearing on the happy path, not just on broken input.
    """
    creds = _refreshable_creds(tmp_path)
    echoed = json.dumps({"access_token": "SECRET_ACCESS", "token_type": "bearer"})
    fake = _token_endpoint_replying(200, json_body={"error": "invalid_client", "error_description": echoed})

    with pytest.raises(_bridge.ReauthenticationRequired) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    message = str(excinfo.value)
    assert "SECRET_ACCESS" not in message
    assert "invalid_client" in message, "everything before the secret is the diagnostic"


def test_secret_json_re_redacts_a_value_truncated_after_a_backslash(tmp_path):
    """A value cut on a lone trailing backslash has nothing for `\\\\.` to consume.

    The star stops before it and the terminator alternation then fails at that position,
    so without the optional backslash the whole match fails and the value prints verbatim.
    """
    creds = _refreshable_creds(tmp_path)
    body = '{"access_token":"SECRET_ACCESS\\'

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    assert "SECRET_ACCESS" not in str(excinfo.value)


def test_body_excerpt_survives_a_body_nested_deeper_than_the_recursion_limit(tmp_path):
    """The reporting path must not be the thing that crashes.

    `_redact_secrets` spends two frames per level, so it gives out at roughly half the
    nesting `json.loads` accepts — a window where a body the parser took blows up the code
    describing it. The regexes are iterative, so falling through to them keeps redaction.
    """
    creds = _refreshable_creds(tmp_path)
    depth = 700
    # The secret goes first, ahead of the nesting: buried past `_DESCRIBE_LIMIT` it would be
    # cut by truncation, and the assertion below would hold whether or not anything redacted
    # it. In front, only the regex fallback can account for its absence.
    # `token_type` outside the Bearer literal is what fails OAuthToken and lands this on the
    # error path at all — with a valid token response there is nothing to report.
    head = '{"access_token": "SECRET_ACCESS", "token_type": "not-a-bearer", "deep": '
    body = head + '{"a": ' * depth + "null" + "}" * depth + "}"

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200, body))())

    assert "SECRET_ACCESS" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value), "absence must come from redaction, not truncation"


@pytest.mark.parametrize(
    "garbage",
    ["[]", "null", '"a string"', "42"],
    ids=["array", "null", "string", "number"],
)
def test_login_quarantines_a_store_that_is_not_an_object(tmp_path, capsys, garbage):
    """Valid JSON that is not a store is the same dead end, one door over.

    A hand-edit that leaves `[]` or `null` behind parses cleanly, so the quarantine's
    `JSONDecodeError` catch never fired — and the value travelled one more line to die
    inside `data.pop(...)` as a raw TypeError. That is exactly the traceback-with-no-way-out
    the quarantine was written to remove, reached through a shape it did not recognise.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(garbage)
    os.chmod(creds, 0o600)

    @asynccontextmanager
    async def fake_http_fail(*args, **kwargs):
        raise RuntimeError("network error")
        yield  # makes this an async generator; unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())

    quarantined = list(tmp_path.glob("credentials.json.corrupt.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == garbage
    assert "moved to" in capsys.readouterr().err
    assert json.loads(creds.read_text()) == {}


@pytest.mark.parametrize("garbage", ["[]", "null", '"a string"', "42"])
def test_file_load_rejects_a_store_that_is_not_an_object(tmp_path, garbage):
    """The headless readers raise, and raise the type their callers already handle.

    `get_tokens` and friends run with nobody at the keyboard, so "start fresh" is not
    theirs to decide — but dying on `AttributeError: 'list' object has no attribute 'get'`
    says nothing about what is wrong. `JSONDecodeError` is the exception every reader of
    this store already handles for "bytes on disk that are not a store", which is what
    lets `login()`'s quarantine cover this shape without a second except clause.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(garbage)
    os.chmod(creds, 0o600)
    storage = _bridge.FileTokenStorage("acme", creds)

    with pytest.raises(json.JSONDecodeError, match="not a JSON object"):
        storage._load()
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(storage.get_tokens())
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(storage.get_client_info())


def test_pre_flight_refresh_propagates_a_store_that_is_not_an_object(tmp_path):
    """Who raises widened; who quarantines did not.

    `_pre_flight_refresh` runs with nobody at the keyboard, so it must not move anyone's
    file aside — it reports and stops, exactly as it does for unparseable bytes. Pinned
    so a later "fix" does not push the quarantine down into the shared read path.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text("[]")
    os.chmod(creds, 0o600)

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(200))())

    assert not list(tmp_path.glob("credentials.json.corrupt.*"))
    assert creds.read_text() == "[]"


def test_keyring_load_falls_back_when_the_blob_is_not_an_object(tmp_path, monkeypatch):
    """A keyring blob that is not a store lands on the documented fallback, not around it.

    `_keyring_load` catches broadly and downgrades to the hardened file with a warning;
    a non-object blob is a blob that will not parse *as a store*, so raising from
    `_keyring_read_raw` routes it there rather than letting a list reach `.get`.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "from_file"}}}))
    os.chmod(creds, 0o600)

    monkeypatch.setattr(_bridge, "_keyring_read_raw", lambda: _bridge._require_store(json.loads("[]"), "[]"))
    storage = _bridge.FileTokenStorage("acme", creds, backend="keyring")

    with pytest.warns(UserWarning, match="keyring unusable"):
        assert storage._load() == {"acme": {"tokens": {"access_token": "from_file"}}}
    assert storage._backend == "file", "one failure downgrades the instance for good"


def test_login_quarantines_a_store_that_is_not_utf8(tmp_path, capsys):
    """`read_text` runs before `json.loads`, so the decode error is the other half of this.

    A store saved in another encoding, or bytes a filesystem mangled, never reaches
    `json.loads` at all. Catching only `JSONDecodeError` leaves that file killing `login`
    with a traceback — the same dead end, one exception type over.
    """
    creds = tmp_path / "credentials.json"
    creds.write_bytes(b'{"acme": {"tokens": {"access_token": "caf\xc3"}}}')
    os.chmod(creds, 0o600)

    @asynccontextmanager
    async def fake_http_fail(*args, **kwargs):
        raise RuntimeError("network error")
        yield  # makes this an async generator; unreachable

    async def run():
        with (
            patch("mcpgen._bridge._local_callback_server", _fake_callback_server_factory()),
            patch("mcpgen._bridge._open_http", fake_http_fail),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(RuntimeError, match="network error"):
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")

    asyncio.run(run())

    assert len(list(tmp_path.glob("credentials.json.corrupt.*"))) == 1
    assert "moved to" in capsys.readouterr().err


def test_login_stops_when_a_corrupt_store_cannot_be_moved_aside(tmp_path):
    """A quarantine that failed must not be followed by the write it was protecting against.

    Swallowing the rename error prints a promise to keep the old bytes and then saves an
    empty store over them one line later — the data loss the quarantine exists to prevent,
    with a message saying it did not happen.
    """
    creds = tmp_path / "credentials.json"
    garbage = '{"acme": {"tokens": {"access_'
    creds.write_text(garbage)
    os.chmod(creds, 0o600)

    def replace_fails(src, dst):
        raise OSError("sharing violation")

    async def run():
        with (
            patch("mcpgen._bridge.os.replace", replace_fails),
            patch("mcpgen._bridge.OAuthClientProvider", MagicMock()),
        ):
            with pytest.raises(_bridge.LoginWontHelp, match="could not be moved aside") as excinfo:
                await _bridge.login("acme", creds_path=creds, url="https://acme.example.com/mcp")
            # The bare base, not a subclass: PostLoginCheckFailed would assert a token is
            # cached and TokenRefreshUnavailable that the token endpoint answered, and
            # neither happened. Pinned so nobody later "classifies" it into a lie.
            assert type(excinfo.value) is _bridge.LoginWontHelp

    asyncio.run(run())

    assert creds.read_text() == garbage, "the store the quarantine failed to move must survive"


def test_body_excerpt_redacts_client_secret(tmp_path):
    """`client_secret` outlives every token in the set — a dynamic client's secret never expires.

    Two real carriers put it in the text printed to stderr: an RFC 7591 registration response,
    and a gateway that echoes the failed token request back in its error body. The diagnostic
    a reader wants from it is that it was *sent*, which `<redacted>` still says.
    """
    creds = _refreshable_creds(tmp_path)
    fake = _token_endpoint_replying(
        200,
        json_body={
            "error_description": "client authentication failed",
            "registration": {"client_id": "public-id", "client_secret": "SECRET_CLIENT"},
        },
    )

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, fake)())

    message = str(excinfo.value)
    assert "SECRET_CLIENT" not in message
    assert "public-id" in message, "the client_id is the diagnostic, and is not a credential"


def test_body_excerpt_redacts_a_form_encoded_client_secret(tmp_path):
    """The form-encoded door has to close on the same member as the JSON one.

    Both regexes and the recursive pass derive from one frozenset precisely so a member
    cannot be covered on one path and open on the other; this pins that they do.
    """
    creds = _refreshable_creds(tmp_path)
    body = "error=invalid_client&client_secret=SECRET_CLIENT&grant_type=refresh_token"

    with pytest.raises(_bridge.TokenRefreshUnavailable) as excinfo:
        asyncio.run(_run_pre_flight(creds, _token_endpoint_replying(400, body))())

    assert "SECRET_CLIENT" not in str(excinfo.value)
    assert "error=invalid_client" in str(excinfo.value), "the non-secret members are the diagnostic"


def test_file_storage_stages_writes_under_a_pid_unique_name(tmp_path):
    """A fixed ".tmp" lets two mcpgen processes clobber each other's partial write."""
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("acme", creds)
    staged = []

    real_replace = os.replace

    def record(src, dst):
        staged.append(str(src))
        real_replace(src, dst)

    with patch("mcpgen._bridge.os.replace", record):
        storage._save({"acme": {"tokens": {"access_token": "tok"}}})

    assert len(staged) == 1
    assert str(os.getpid()) in staged[0], "concurrent processes must not share a staging path"
    assert not list(tmp_path.glob("*.tmp*")), "the staging file is renamed away, not left behind"
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "tok"


def test_client_config_stages_writes_under_a_pid_unique_name(tmp_path):
    """The client config writer got the same fix and needs the same pin.

    Two files, one convention: leaving either on a fixed ".tmp" reinstates the
    collision for that file alone, which is exactly the kind of asymmetry a later
    refactor restores by accident.
    """
    target = tmp_path / "config.json"
    staged = []

    real_replace = os.replace

    def record(src, dst):
        staged.append(str(src))
        real_replace(src, dst)

    with patch("mcpgen._bridge.os.replace", record):
        _bridge._save_client_config({"cred_backend": "keyring"}, target)

    assert len(staged) == 1
    assert str(os.getpid()) in staged[0], "concurrent processes must not share a staging path"
    assert not list(tmp_path.glob("*.tmp*"))
    assert json.loads(target.read_text())["cred_backend"] == "keyring"


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
    from mcp.shared.auth import OAuthClientInformationFull

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

    token_body = json.dumps({"access_token": "issued_tok", "token_type": "Bearer", "expires_in": 3600}).encode()

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
        # `from None` at the raise site, pinned here because this is the only test that
        # reaches it: the annotated text is redacted, and chaining the original would put
        # the unredacted registration body — `client_secret` included — back in front of
        # anyone printing a traceback. This type sits outside the `LoginWontHelp` taxonomy,
        # so it escapes both `_cmd_login` and `main()`: a traceback is its normal rendering.
        assert exc.value.__cause__ is None
        assert exc.value.__suppress_context__ is True
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
    login_mock = _login_that_writes(creds)

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
    login_mock = _login_that_writes(creds)

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


# ---------------------------------------------------------------------------
# Concurrent credential writes: the store lock and the _mutate seam
# ---------------------------------------------------------------------------


def _run_concurrently(fns, stagger=0.05):
    """Start each callable on its own thread, `stagger` seconds apart, and join.

    Threads rather than processes on purpose: `flock` is held per open file
    description, not per process, so two `open()` calls from one interpreter
    contend exactly as two interpreters would. That keeps the test honest
    without paying for a process spawn.
    """
    import threading

    errors: list[BaseException] = []

    def guard(fn):
        def run():
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 — surfaced by the assert below
                errors.append(exc)

        return run

    # Daemon threads: a worker that deadlocks on the lock never returns, and a
    # non-daemon one would hold the interpreter open at exit — so a regression would
    # hang the suite forever instead of failing the assert below.
    threads = [threading.Thread(target=guard(fn), daemon=True) for fn in fns]
    for t in threads:
        t.start()
        time.sleep(stagger)
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a worker thread deadlocked"
    assert not errors, f"worker raised: {errors!r}"


def test_concurrent_set_tokens_for_different_servers_keep_both_entries(tmp_path):
    """The reported bug: two processes read-modify-write the whole store, last wins.

    Without serialisation the second writer's snapshot predates the first
    writer's `os.replace`, so the first server's freshly-issued token is
    silently dropped and its next run goes back to the browser.
    """
    creds = tmp_path / "credentials.json"
    real_load = _bridge.FileTokenStorage._file_load

    def slow_load(self):
        data = real_load(self)
        time.sleep(0.2)  # widen the read-modify-write window past the stagger
        return data

    def writer(name):
        def run():
            storage = _bridge.FileTokenStorage(name, credentials_path=creds, backend="file")
            asyncio.run(storage.set_tokens(OAuthToken(access_token=f"tok-{name}", token_type="bearer")))

        return run

    with patch.object(_bridge.FileTokenStorage, "_file_load", slow_load):
        _run_concurrently([writer("alpha"), writer("beta")])

    stored = json.loads(creds.read_text())
    assert stored["alpha"]["tokens"]["access_token"] == "tok-alpha"
    assert stored["beta"]["tokens"]["access_token"] == "tok-beta"


def test_concurrent_keyring_writes_keep_both_entries(tmp_path):
    """The keyring backend has the same read-modify-write shape and needs the same lock.

    A fix that only covers the file backend leaves half the surface open, so the
    lock file — which exists on disk regardless of backend — is the rendezvous
    for both.
    """
    fake_kr = _FakeKeyring()
    creds = tmp_path / "credentials.json"
    real_read = _bridge._keyring_read_raw

    def slow_read():
        data = real_read()
        time.sleep(0.2)
        return data

    def writer(name):
        def run():
            storage = _bridge.FileTokenStorage(name, credentials_path=creds, backend="keyring")
            asyncio.run(storage.set_tokens(OAuthToken(access_token=f"tok-{name}", token_type="bearer")))

        return run

    with patch.dict("sys.modules", {"keyring": fake_kr}), patch.object(_bridge, "_keyring_read_raw", slow_read):
        _run_concurrently([writer("alpha"), writer("beta")])

    stored = json.loads(fake_kr._store[(_bridge._KEYRING_SERVICE, _bridge._KEYRING_USER)])
    assert stored["alpha"]["tokens"]["access_token"] == "tok-alpha"
    assert stored["beta"]["tokens"]["access_token"] == "tok-beta"


def test_concurrent_token_endpoint_writes_keep_both_entries(tmp_path):
    """_persist_token_endpoint is a read-modify-write of the whole store too."""
    creds = tmp_path / "credentials.json"
    real_load = _bridge.FileTokenStorage._file_load

    def slow_load(self):
        data = real_load(self)
        time.sleep(0.2)
        return data

    def writer(name):
        def run():
            storage = _bridge.FileTokenStorage(name, credentials_path=creds, backend="file")
            provider = SimpleNamespace(_get_token_endpoint=lambda: f"https://{name}.example/token")
            _bridge._persist_token_endpoint(storage, name, provider)

        return run

    with patch.object(_bridge.FileTokenStorage, "_file_load", slow_load):
        _run_concurrently([writer("alpha"), writer("beta")])

    stored = json.loads(creds.read_text())
    assert stored["alpha"]["token_endpoint"] == "https://alpha.example/token"
    assert stored["beta"]["token_endpoint"] == "https://beta.example/token"


def test_mutate_reads_the_store_fresh_inside_the_lock(tmp_path):
    """The callback must see the store as of lock acquisition, not an earlier read.

    A `_mutate` that merged into a dict captured before the lock would reinstate
    the lost update it exists to prevent.
    """
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("alpha", credentials_path=creds, backend="file")
    storage._save({"beta": {"tokens": {"access_token": "written-by-someone-else"}}})

    seen: list[dict] = []

    def add_alpha(data):
        seen.append(json.loads(json.dumps(data)))
        data.setdefault("alpha", {})["tokens"] = {"access_token": "tok"}
        return "result"

    assert storage._mutate(add_alpha) == "result", "_mutate returns the callback's value"
    assert seen == [{"beta": {"tokens": {"access_token": "written-by-someone-else"}}}]
    assert json.loads(creds.read_text())["beta"]["tokens"]["access_token"] == "written-by-someone-else"


def test_mutate_does_not_write_when_the_callback_raises(tmp_path):
    """A callback that fails half-way must leave the store as it found it."""
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("alpha", credentials_path=creds, backend="file")
    storage._save({"beta": {"tokens": {"access_token": "keep-me"}}})

    def boom(data):
        data.clear()
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        storage._mutate(boom)

    assert json.loads(creds.read_text()) == {"beta": {"tokens": {"access_token": "keep-me"}}}


def test_store_lock_serialises_two_open_file_descriptions(tmp_path):
    """Two holders of the lock must not be inside it at once."""
    creds = tmp_path / "credentials.json"
    overlap = []
    inside = []

    def hold():
        with _bridge._store_lock(creds):
            inside.append(1)
            overlap.append(len(inside))
            time.sleep(0.2)
            inside.pop()

    _run_concurrently([hold, hold])
    assert overlap == [1, 1], "the lock allowed two holders inside at once"


def test_store_lock_is_reentrant_within_one_thread(tmp_path):
    """A nested acquire must not self-deadlock.

    `flock` is per open file description, so a second `open()` + `LOCK_EX` from a
    thread that already holds the lock blocks on itself forever. Any future
    `_mutate` nested inside another would hang rather than fail.
    """
    creds = tmp_path / "credentials.json"
    reached = []

    def nest():
        with _bridge._store_lock(creds):
            with _bridge._store_lock(creds):
                reached.append(True)

    _run_concurrently([nest])
    assert reached == [True]


def test_store_lock_file_is_0600_in_a_0700_directory(tmp_path):
    """The lock file sits next to the credentials and gets the same hardening."""
    creds = tmp_path / "sub" / "credentials.json"
    with _bridge._store_lock(creds):
        pass
    lock = creds.with_name(creds.name + ".lock")
    assert lock.exists(), "the lock file must be created next to the store"
    assert stat.S_IMODE(os.stat(lock).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(lock.parent).st_mode) == 0o700


def test_store_lock_degrades_to_a_noop_without_a_platform_primitive(tmp_path):
    """No fcntl and no msvcrt: yield anyway rather than fail every credential write."""
    creds = tmp_path / "credentials.json"
    with patch.object(_bridge, "_lock_fd", None):
        with _bridge._store_lock(creds):
            pass
    assert not list(tmp_path.iterdir()), "a no-op lock must not leave a lock file behind"


_LOGIN_FAILURE_PATH_READS = 3
"""How many times a failing login() reads the whole store for its own server.

1 is the stash-pop at the top (locked), 2 is the failure handler's branch-deciding
read (deliberately *unlocked* — it only picks a branch and writes nothing), 3 is the
restore's read inside `_mutate` (locked). `_login_racing_a_foreign_writer` aims its
race window by this numbering, so a refactor that adds or drops a read moves every
window; the helper asserts the count rather than trusting it.
"""


def _login_racing_a_foreign_writer(tmp_path, window_call_index):
    """Run a failing login() while another mcpgen writes a *different* server.

    `window_call_index` picks which of login()'s store reads to open the race in —
    see `_LOGIN_FAILURE_PATH_READS` for the numbering. Only the two locked reads (1
    and 3) are worth aiming at: they open a read-modify-write cycle, and the competing
    writer is released from inside the window and then races the save that closes it,
    so an unserialised cycle loses the foreign entry and a serialised one makes the
    writer wait its turn. Aiming at 2 tests nothing — the restore re-reads afterwards
    either way, so the assertion holds with the lock removed.

    Returns the store as it ends up on disk.
    """
    import threading

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "orig_tok", "token_type": "bearer"}}}))
    os.chmod(creds, 0o600)

    window_open = threading.Event()
    reads = 0
    real_load = _bridge.FileTokenStorage._file_load

    def load_then_open_the_window(self):
        data = real_load(self)
        # Count only the login's own reads. `_file_load` is patched at class level, so
        # the racing writer's own load runs through here too — counting it would shift
        # the index after the window opens and aim later windows at the wrong call site.
        if self._key == "acme":
            nonlocal reads
            reads += 1
            if reads == window_call_index:
                window_open.set()
                time.sleep(0.2)
        return data

    def foreign_writer():
        # Hard-fail rather than fall through: `window_call_index` is coupled to an exact
        # count of internal reads, so a refactor that adds one would silently aim this
        # test at the wrong window and pass without racing anything.
        assert window_open.wait(timeout=5), "the race window never opened"
        other = _bridge.FileTokenStorage("other", credentials_path=creds, backend="file")
        asyncio.run(other.set_tokens(OAuthToken(access_token="tok-other", token_type="bearer")))

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

    writer = threading.Thread(target=foreign_writer, daemon=True)
    with patch.object(_bridge.FileTokenStorage, "_file_load", load_then_open_the_window):
        writer.start()
        asyncio.run(run())
        writer.join(timeout=10)
    assert not writer.is_alive(), "the competing writer deadlocked"
    # The index above names one specific read. A refactor that adds or removes one
    # renumbers the rest, and the window would silently move to a call site this test
    # does not claim to cover — passing without exercising the lock at all.
    assert reads == _LOGIN_FAILURE_PATH_READS, (
        f"login()'s failure path read the store {reads} times, not {_LOGIN_FAILURE_PATH_READS}; "
        "re-check which read `window_call_index` now names"
    )
    return json.loads(creds.read_text())


def test_login_stash_does_not_drop_a_concurrent_write_to_another_server(tmp_path):
    """login() clears its own entry by saving the whole store — under the lock."""
    stored = _login_racing_a_foreign_writer(tmp_path, window_call_index=1)
    assert stored["other"]["tokens"]["access_token"] == "tok-other"


def test_login_restore_does_not_drop_a_concurrent_write_to_another_server(tmp_path):
    """The failure handler's restore is a whole-store write too, and races the same way.

    Window 3, not 2: 2 is the handler's unlocked branch-deciding read, and the restore
    re-reads after it, so a race opened there is repaired by the very read under test.
    """
    stored = _login_racing_a_foreign_writer(tmp_path, window_call_index=3)
    assert stored["other"]["tokens"]["access_token"] == "tok-other"
    assert stored["acme"]["tokens"]["access_token"] == "orig_tok", "the restore itself must still happen"


def test_delete_cred_does_not_destroy_a_concurrently_written_entry(tmp_path):
    """delete_cred clears the whole store when its (stale) view says nothing is left.

    Reading outside a lock makes that decision on a snapshot: a login that lands
    between the read and the unlink writes an entry into a file `delete_cred` is
    about to delete, and the credential is gone with no error anywhere.
    """
    import threading

    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "orig"}}}))
    os.chmod(creds, 0o600)

    window_open = threading.Event()
    real_read = _bridge._read_backend

    def slow_read(backend, path):
        data = real_read(backend, path)
        window_open.set()
        time.sleep(0.2)
        return data

    def foreign_writer():
        # Hard-fail rather than fall through: a window that never opens would otherwise
        # let this test pass without ever having raced anything.
        assert window_open.wait(timeout=5), "the race window never opened"
        other = _bridge.FileTokenStorage("other", credentials_path=creds, backend="file")
        asyncio.run(other.set_tokens(OAuthToken(access_token="tok-other", token_type="bearer")))

    writer = threading.Thread(target=foreign_writer, daemon=True)
    with patch.object(_bridge, "_read_backend", slow_read):
        writer.start()
        assert _bridge.delete_cred("acme", backend="file", credentials_path=creds) is True
        writer.join(timeout=10)
    assert not writer.is_alive(), "the competing writer deadlocked"

    assert creds.exists(), "the store was cleared on a stale view of what was left"
    stored = json.loads(creds.read_text())
    assert stored["other"]["tokens"]["access_token"] == "tok-other"
    assert "acme" not in stored, "the requested deletion must still happen"


def test_migrate_purge_does_not_drop_a_concurrent_write_to_the_source(tmp_path):
    """The purge re-reads the source, and that read races every other mcpgen write."""
    import threading

    fake_kr = _FakeKeyringMig()
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "orig"}}}))
    os.chmod(creds, 0o600)

    window_open = threading.Event()
    real_read = _bridge._read_backend
    file_reads = itertools.count(1)

    def slow_read(backend, path):
        data = real_read(backend, path)
        # The second file read is the purge's; the window that matters is between it
        # and the write that follows, not between the first read and anything.
        if backend == "file" and next(file_reads) == 2:
            window_open.set()
            time.sleep(0.2)
        return data

    def foreign_writer():
        # Hard-fail rather than fall through: a window that never opens would otherwise
        # let this test pass without ever having raced anything.
        assert window_open.wait(timeout=5), "the race window never opened"
        other = _bridge.FileTokenStorage("other", credentials_path=creds, backend="file")
        asyncio.run(other.set_tokens(OAuthToken(access_token="tok-other", token_type="bearer")))

    writer = threading.Thread(target=foreign_writer, daemon=True)
    with patch.dict("sys.modules", {"keyring": fake_kr}), patch.object(_bridge, "_read_backend", slow_read):
        writer.start()
        _bridge.migrate_creds(
            from_backend="file",
            to_backend="keyring",
            purge=True,
            credentials_path=creds,
            config_path=tmp_path / "config.json",
        )
        writer.join(timeout=10)
    assert not writer.is_alive(), "the competing writer deadlocked"

    stored = json.loads(creds.read_text())
    assert stored["other"]["tokens"]["access_token"] == "tok-other"
    assert "acme" not in stored, "the migrated entry must still be purged"


_CONCURRENT_WRITER = """
import asyncio, sys, time
from pathlib import Path
from unittest.mock import patch
from mcp.shared.auth import OAuthToken
from mcpgen import _bridge

name, creds = sys.argv[1], Path(sys.argv[2])
real = _bridge.FileTokenStorage._file_load


def slow(self):
    data = real(self)
    time.sleep(0.4)
    return data


with patch.object(_bridge.FileTokenStorage, "_file_load", slow):
    storage = _bridge.FileTokenStorage(name, credentials_path=creds, backend="file")
    asyncio.run(storage.set_tokens(OAuthToken(access_token="tok-" + name, token_type="bearer")))
"""


def test_two_processes_writing_different_servers_keep_both_entries(tmp_path):
    """The reported bug was two processes, and only two processes prove the fix.

    The threaded tests exercise the same primitive — `flock` is held per open file
    description, so two `open()` calls contend whether or not they share an
    interpreter — but they cannot catch a fix that accidentally depends on shared
    process state. This one can, at the cost of two interpreter startups.
    """
    import subprocess

    creds = tmp_path / "credentials.json"
    script = tmp_path / "writer.py"
    script.write_text(_CONCURRENT_WRITER)

    procs = [subprocess.Popen([sys.executable, str(script), name, str(creds)]) for name in ("alpha", "beta")]
    try:
        for proc in procs:
            assert proc.wait(timeout=60) == 0, "writer process failed"
    finally:
        # A timeout here means one writer is blocked on a lock it will never get.
        # Leaving it running would outlive the test and hold that lock against the
        # rest of the suite.
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    stored = json.loads(creds.read_text())
    assert stored["alpha"]["tokens"]["access_token"] == "tok-alpha"
    assert stored["beta"]["tokens"]["access_token"] == "tok-beta"


def test_store_lock_leaves_an_existing_directory_alone(tmp_path):
    """Taking a lock must not chmod a directory it did not create.

    `_file_save` hardens the parent because secrets land in it. The lock file is
    empty and 0600 and protects nothing, so doing it here reached paths that never
    write — including the current working directory, when `--creds` is relative.
    """
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    os.chmod(loose, 0o755)  # mkdir's mode is masked by umask; set it outright
    with _bridge._store_lock(loose / "credentials.json"):
        pass
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o755, "an existing directory must not be re-moded"


def test_store_lock_creates_its_own_directory_private(tmp_path):
    """A directory the lock itself creates is born 0700 — it may hold the store next."""
    creds = tmp_path / "fresh" / "credentials.json"
    with _bridge._store_lock(creds):
        pass
    assert stat.S_IMODE(os.stat(creds.parent).st_mode) == 0o700


def test_store_lock_reports_and_proceeds_when_it_cannot_be_created(tmp_path, capsys):
    """A lock that cannot be set up is a lost update, never a failed credential write.

    Under `simplefilter("error")` — `-W error`, or a downstream suite running with
    `filterwarnings = error` — a `warnings.warn` here would raise and abort the very
    write this branch exists to keep working. So it goes to stderr, for the same
    reason the corrupt-store quarantine message does: it has to reach the operator
    whatever their warnings filter says, and it must never be fatal.
    """
    unwritable = tmp_path / "ro"
    unwritable.mkdir()
    os.chmod(unwritable, 0o500)
    reached = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with _bridge._store_lock(unwritable / "sub" / "credentials.json"):
                reached.append(True)
    finally:
        os.chmod(unwritable, 0o700)
    assert reached == [True], "the body must run even though the lock could not be created"
    assert "proceeding without cross-process locking" in capsys.readouterr().err


def test_store_lock_reports_and_proceeds_when_the_acquire_fails(tmp_path, capsys):
    """`flock` raises ENOLCK on some NFS mounts, and Windows `LK_LOCK` gives up after ~10s.

    Both are the same situation as having no primitive at all, and the module's
    policy for that is to degrade rather than fail the write.
    """
    creds = tmp_path / "credentials.json"

    def refuse(fd):
        raise OSError(_errno.ENOLCK, "No locks available")

    reached = []
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with patch.object(_bridge, "_lock_fd", refuse):
            with _bridge._store_lock(creds):
                reached.append(True)
    assert reached == [True], "a refused acquire must not abort the write"
    assert "proceeding without cross-process locking" in capsys.readouterr().err


def test_keyring_set_tokens_survives_a_credentials_dir_it_cannot_create(tmp_path, capsys):
    """The keyring backend never touched disk before; a lock must not change that.

    Raising here puts a raw OSError in the middle of the SDK's httpx auth
    handshake, replacing a login that used to work.
    """
    fake_kr = _FakeKeyring()
    unwritable = tmp_path / "ro"
    unwritable.mkdir()
    os.chmod(unwritable, 0o500)
    creds = unwritable / "sub" / "credentials.json"
    try:
        with patch.dict("sys.modules", {"keyring": fake_kr}), warnings.catch_warnings():
            warnings.simplefilter("error")  # the degrade must survive a fatal warnings filter
            storage = _bridge.FileTokenStorage("srv", credentials_path=creds, backend="keyring")
            asyncio.run(storage.set_tokens(OAuthToken(access_token="kr_token", token_type="bearer")))
    finally:
        os.chmod(unwritable, 0o700)
    stored = json.loads(fake_kr._store[(_bridge._KEYRING_SERVICE, _bridge._KEYRING_USER)])
    assert stored["srv"]["tokens"]["access_token"] == "kr_token"
    assert "proceeding without cross-process locking" in capsys.readouterr().err, (
        "the degrade branch must actually have been reached"
    )


def test_delete_cred_of_an_absent_name_still_reports_false(tmp_path):
    """The lock is taken before the read that decides this, and that ordering is the fix."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "t"}}}))
    os.chmod(creds, 0o600)
    assert _bridge.delete_cred("nope", backend="file", credentials_path=creds) is False
    assert json.loads(creds.read_text())["acme"]["tokens"]["access_token"] == "t"


def test_store_lock_reentrancy_survives_a_differently_spelled_path(tmp_path):
    """Two spellings of one file must be one key, or the nested acquire self-deadlocks.

    `flock` is per open file description: a miss opens a second descriptor to a lock
    this thread already holds and blocks on it forever. Cross-*process* exclusion is
    unaffected either way — different spellings resolve to the same inode — so only
    the in-process guard needs the resolution.
    """
    creds = tmp_path / "credentials.json"
    (tmp_path / "sub").mkdir()  # pathlib keeps "..", so this is a genuinely other spelling
    reached = []

    def nest():
        with _bridge._store_lock(creds):
            with _bridge._store_lock(tmp_path / "sub" / ".." / "credentials.json"):
                reached.append(True)

    _run_concurrently([nest])
    assert reached == [True]


def test_mutate_reads_the_store_only_once_it_holds_the_lock(tmp_path):
    """Not just "fresh" — fresh *as of the acquire*, which is the whole fix.

    A `_mutate` that read the store and only then blocked on the lock would look
    correct in a single-threaded test and still lose every update it was written to
    prevent. So the competing write lands while this caller is already waiting.
    """
    import threading

    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("alpha", credentials_path=creds, backend="file")
    storage._save({"beta": {"tokens": {"access_token": "v1"}}})

    holding = threading.Event()

    def holder():
        with _bridge._store_lock(creds):
            holding.set()
            time.sleep(0.1)  # long enough for the caller below to be blocked on the lock
            # `_save` does not lock — this is the write of a holder that already has it.
            storage._save({"beta": {"tokens": {"access_token": "v2"}}})
            time.sleep(0.05)

    seen: list[dict] = []
    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert holding.wait(timeout=5), "the holder never took the lock"
    storage._mutate(lambda data: seen.append(json.loads(json.dumps(data))))
    thread.join(timeout=10)
    assert not thread.is_alive()

    assert seen == [{"beta": {"tokens": {"access_token": "v2"}}}], "_mutate read the store before it held the lock"


def test_concurrent_keyring_writes_at_different_creds_paths_keep_both_entries(tmp_path):
    """The keyring is one global item; the file path is not its identity.

    `--creds` is accepted on every command and documented as ignored by the keyring
    backend, so two keyring processes may legitimately carry different paths. Keying
    the lock on the path alone puts them on different sidecars while they read and
    rewrite the same keyring document — no exclusion at all, on the backend the
    whole both-backends claim rests on.
    """
    fake_kr = _FakeKeyring()
    real_read = _bridge._keyring_read_raw

    def slow_read():
        data = real_read()
        time.sleep(0.2)
        return data

    def writer(name, creds):
        def run():
            storage = _bridge.FileTokenStorage(name, credentials_path=creds, backend="keyring")
            asyncio.run(storage.set_tokens(OAuthToken(access_token=f"tok-{name}", token_type="bearer")))

        return run

    with (
        patch.dict("sys.modules", {"keyring": fake_kr}),
        patch.object(_bridge, "_keyring_read_raw", slow_read),
    ):
        _run_concurrently(
            [
                writer("alpha", tmp_path / "alpha-creds.json"),
                writer("beta", tmp_path / "beta-creds.json"),
            ]
        )

    stored = json.loads(fake_kr._store[(_bridge._KEYRING_SERVICE, _bridge._KEYRING_USER)])
    assert stored["alpha"]["tokens"]["access_token"] == "tok-alpha"
    assert stored["beta"]["tokens"]["access_token"] == "tok-beta"


def test_migrate_holds_the_keyring_lock_against_a_writer_at_another_path(tmp_path):
    """A migration always pairs the keyring with the file, so it needs both locks."""
    fake_kr = _FakeKeyringMig()
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": {"tokens": {"access_token": "orig"}}}))
    os.chmod(creds, 0o600)

    window_open = threading.Event()
    real_read = _bridge._keyring_read_raw

    def slow_read():
        data = real_read()
        window_open.set()
        time.sleep(0.2)
        return data

    def foreign_writer():
        assert window_open.wait(timeout=5), "the race window never opened"
        other = _bridge.FileTokenStorage("other", credentials_path=tmp_path / "other-creds.json", backend="keyring")
        asyncio.run(other.set_tokens(OAuthToken(access_token="tok-other", token_type="bearer")))

    writer = threading.Thread(target=foreign_writer, daemon=True)
    with (
        patch.dict("sys.modules", {"keyring": fake_kr}),
        patch.object(_bridge, "_keyring_read_raw", slow_read),
    ):
        writer.start()
        _bridge.migrate_creds(
            from_backend="file",
            to_backend="keyring",
            credentials_path=creds,
            config_path=tmp_path / "config.json",
        )
        writer.join(timeout=10)
    assert not writer.is_alive(), "the competing writer deadlocked"

    stored = json.loads(fake_kr._store[(_bridge._KEYRING_SERVICE, _bridge._KEYRING_USER)])
    assert stored["other"]["tokens"]["access_token"] == "tok-other"
    assert stored["acme"]["tokens"]["access_token"] == "orig", "the migration must still land"


# ---------------------------------------------------------------------------
# _pre_flight_refresh: the write must still belong to the credential it started from
# ---------------------------------------------------------------------------


def _expired_entry(refresh_token="old-rt"):
    return {
        "tokens": {
            "access_token": "expired",
            "token_type": "Bearer",
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) - 10,
        },
        "client_info": {"client_id": "cid"},
        "token_endpoint": "https://auth.example/token",
    }


def _refresh_racing(creds, during_post):
    """Drive _pre_flight_refresh with *during_post* running inside the network round.

    The lock cannot cover this window — it is an HTTP round-trip — so what the
    callback does is exactly what a concurrent login or second refresh does: install
    a newer credential while this one is waiting on the wire.
    """

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data, headers=None):
            during_post()
            return SimpleNamespace(
                status_code=200,
                text="",
                headers={},
                json=lambda: {
                    "access_token": "late-old-refresh",
                    "token_type": "Bearer",
                    "refresh_token": "rotated-rt",
                },
            )

    async def run():
        storage = _bridge.FileTokenStorage("acme", creds)
        with patch("mcpgen._bridge.httpx.AsyncClient", _FakeClient):
            await _bridge._pre_flight_refresh("acme", storage)

    asyncio.run(run())


def test_pre_flight_refresh_does_not_overwrite_a_credential_that_arrived_meanwhile(tmp_path):
    """A login landing inside the refresh round-trip must survive it.

    The response chains from the refresh token this call read *before* the request.
    Under refresh-token rotation the authorization server invalidates that chain when
    it issues the newer one, so writing the late response over the login does not
    merely lose an update — it caches a credential that is already dead, and the next
    run goes to the browser. Same shape as `login()`'s restore, one window over.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": _expired_entry()}))
    os.chmod(creds, 0o600)

    def concurrent_login():
        # Through the same seam a real login writes, but synchronously: this runs
        # inside the caller's event loop, where asyncio.run() cannot.
        other = _bridge.FileTokenStorage("acme", credentials_path=creds, backend="file")
        other._mutate(
            lambda data: data.setdefault("acme", {}).__setitem__(
                "tokens", {"access_token": "new-login", "token_type": "Bearer", "refresh_token": "new-rt"}
            )
        )

    _refresh_racing(creds, concurrent_login)

    stored = json.loads(creds.read_text())["acme"]["tokens"]
    assert stored["access_token"] == "new-login", "the newer credential must win"
    assert stored["refresh_token"] == "new-rt"


def test_pre_flight_refresh_does_not_resurrect_a_credential_deleted_meanwhile(tmp_path):
    """`delete_cred` landing inside the round-trip must not be undone by the response."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": _expired_entry(), "other": {"tokens": {"access_token": "keep"}}}))
    os.chmod(creds, 0o600)

    def concurrent_delete():
        _bridge.delete_cred("acme", backend="file", credentials_path=creds)

    _refresh_racing(creds, concurrent_delete)

    stored = json.loads(creds.read_text())
    assert "acme" not in stored, "a deleted credential must not come back"
    assert stored["other"]["tokens"]["access_token"] == "keep"


def test_pre_flight_refresh_stores_the_new_token_when_nothing_raced_it(tmp_path):
    """The uncontended path must still write — a veto that fires always is no fix."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": _expired_entry()}))
    os.chmod(creds, 0o600)

    _refresh_racing(creds, lambda: None)

    stored = json.loads(creds.read_text())["acme"]["tokens"]
    assert stored["access_token"] == "late-old-refresh"
    assert stored["refresh_token"] == "rotated-rt"


# ---------------------------------------------------------------------------
# A refresh response may legally omit refresh_token (RFC 6749 §6)
# ---------------------------------------------------------------------------


def test_set_tokens_keeps_a_refresh_token_the_response_omitted(tmp_path):
    """§6 makes the refresh token optional in a refresh response; Google omits it.

    Writing the entry wholesale erases the stored one, and the next expiry then has
    nothing to refresh with — a browser prompt every token lifetime, on a server that
    did nothing wrong.
    """
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("acme", credentials_path=creds, backend="file")
    storage._save({"acme": {"tokens": {"access_token": "old", "token_type": "Bearer", "refresh_token": "RT"}}})

    asyncio.run(storage.set_tokens(OAuthToken(access_token="new", token_type="bearer", expires_in=3600)))

    stored = json.loads(creds.read_text())["acme"]["tokens"]
    assert stored["access_token"] == "new"
    assert stored["refresh_token"] == "RT", "an omitted refresh token means keep using the one you have"


def test_set_tokens_replaces_a_rotated_refresh_token(tmp_path):
    """§6 also says: when the server *does* issue a new one, discard the old."""
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("acme", credentials_path=creds, backend="file")
    storage._save({"acme": {"tokens": {"access_token": "old", "token_type": "Bearer", "refresh_token": "RT"}}})

    asyncio.run(storage.set_tokens(OAuthToken(access_token="new", token_type="bearer", refresh_token="RT2")))

    assert json.loads(creds.read_text())["acme"]["tokens"]["refresh_token"] == "RT2"


def test_set_tokens_does_not_invent_a_refresh_token_for_a_new_entry(tmp_path):
    """Carrying forward must mean carrying, not fabricating."""
    creds = tmp_path / "credentials.json"
    storage = _bridge.FileTokenStorage("acme", credentials_path=creds, backend="file")

    asyncio.run(storage.set_tokens(OAuthToken(access_token="new", token_type="bearer")))

    assert "refresh_token" not in json.loads(creds.read_text())["acme"]["tokens"]


def test_pre_flight_refresh_keeps_the_refresh_token_when_the_response_omits_it(tmp_path):
    """The whole point of the compare-and-set key is that it must survive its own write."""
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": _expired_entry()}))
    os.chmod(creds, 0o600)

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data, headers=None):
            return SimpleNamespace(
                status_code=200,
                text="",
                headers={},
                json=lambda: {"access_token": "NEW", "token_type": "Bearer", "expires_in": 3600},
            )

    async def run():
        storage = _bridge.FileTokenStorage("acme", creds)
        with patch("mcpgen._bridge.httpx.AsyncClient", _FakeClient):
            await _bridge._pre_flight_refresh("acme", storage)

    asyncio.run(run())
    stored = json.loads(creds.read_text())["acme"]["tokens"]
    assert stored["access_token"] == "NEW"
    assert stored["refresh_token"] == "old-rt", "the next refresh must still have something to send"


def test_store_lock_degrades_on_a_path_with_no_sidecar_name(tmp_path, capsys):
    """Deriving the sidecar name can fail too, and that is still not a failed write.

    `Path("/").with_name(...)` raises `ValueError`, not `OSError`. Left outside the
    guard it escapes as a traceback — from a keyring operation, where `--creds` is
    documented as ignored, so the path that broke it was never even used.
    """
    reached = []
    with _bridge._store_lock(Path("/")):
        reached.append(True)
    assert reached == [True]
    assert "proceeding without cross-process locking" in capsys.readouterr().err
