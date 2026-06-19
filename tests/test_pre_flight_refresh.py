"""Unit tests for _bridge._pre_flight_refresh — the load-bearing OAuth token
refresh that runs out-of-band before a session opens.

This function works around an SDK bug (the official ``mcp`` silent-refresh
branch is unreachable at cold start), so a regression here surfaces as a
surprise browser re-auth rather than a clean error. It has many branches and
no network of its own beyond a single token-endpoint POST, so every path is
covered here with a real on-disk FileTokenStorage and a mocked
``httpx.AsyncClient`` — no live network.

Async helpers are invoked via asyncio.run() (matching the project convention —
no pytest-asyncio dependency needed).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcpgen import _bridge

SERVER = "acme"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _storage(tmp_path):
    return _bridge.FileTokenStorage(
        SERVER, credentials_path=tmp_path / "credentials.json", backend="file"
    )


def _seed(storage, entry: dict) -> None:
    """Write a single-server credentials entry via the hardened file backend."""
    storage._save({SERVER: entry})


def _patch_httpx(status_code=200, json_payload=None, text="", capture=None):
    """Patch ``_bridge.httpx.AsyncClient`` with a mock recording the POST.

    Returns ``(patcher, client_mock)`` so callers can assert on the recorded
    request or that no request was made at all.
    """
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=json_payload or {})
    response.text = text

    async def fake_post(endpoint, data=None):
        if capture is not None:
            capture["endpoint"] = endpoint
            capture["data"] = data
        return response

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    patcher = patch("mcpgen._bridge.httpx.AsyncClient", MagicMock(return_value=client))
    return patcher, client


def _run(server, storage):
    return asyncio.run(_bridge._pre_flight_refresh(server, storage))


# ---------------------------------------------------------------------------
# Early-return branches: no network should be touched
# ---------------------------------------------------------------------------


def test_fresh_token_is_noop(tmp_path):
    """A token far from expiry must short-circuit before any HTTP call."""
    storage = _storage(tmp_path)
    _seed(
        storage,
        {"tokens": {"access_token": "fresh", "token_type": "bearer", "expires_at": time.time() + 7200}},
    )
    patcher, client = _patch_httpx()
    with patcher:
        _run(SERVER, storage)
    client.post.assert_not_called()
    assert storage._load()[SERVER]["tokens"]["access_token"] == "fresh"


def test_no_expiry_is_noop(tmp_path):
    """A token without expires_at carries no expiry info → nothing to refresh."""
    storage = _storage(tmp_path)
    _seed(storage, {"tokens": {"access_token": "noexp", "token_type": "bearer"}})
    patcher, client = _patch_httpx()
    with patcher:
        _run(SERVER, storage)
    client.post.assert_not_called()


def test_unknown_server_is_noop(tmp_path):
    """A server with no stored entry has no tokens → early return, no HTTP."""
    storage = _storage(tmp_path)
    _seed(storage, {"tokens": {"access_token": "x", "token_type": "bearer"}})
    patcher, client = _patch_httpx()
    with patcher:
        asyncio.run(_bridge._pre_flight_refresh("other-server", storage))
    client.post.assert_not_called()


def test_within_margin_triggers_refresh(tmp_path):
    """A token inside the _MARGIN window is treated as needing refresh."""
    storage = _storage(tmp_path)
    # expires in 60s, margin is 120s → due for refresh.
    _seed(
        storage,
        {
            "tokens": {
                "access_token": "soon",
                "token_type": "bearer",
                "refresh_token": "rt",
                "expires_at": time.time() + 60,
            },
            "client_info": {"client_id": "cid"},
            "token_endpoint": "https://auth.example.com/token",
        },
    )
    patcher, client = _patch_httpx(
        json_payload={"access_token": "renewed", "token_type": "bearer"}
    )
    with patcher:
        _run(SERVER, storage)
    client.post.assert_called_once()
    assert storage._load()[SERVER]["tokens"]["access_token"] == "renewed"


# ---------------------------------------------------------------------------
# Missing-prerequisite branches: each raises ReauthenticationRequired
# ---------------------------------------------------------------------------


def _expired_entry(**overrides):
    entry = {
        "tokens": {
            "access_token": "stale",
            "token_type": "bearer",
            "refresh_token": "rt",
            "expires_at": time.time() - 100,
        },
        "client_info": {"client_id": "cid"},
        "token_endpoint": "https://auth.example.com/token",
    }
    entry.update(overrides)
    return entry


def test_missing_refresh_token_raises(tmp_path):
    storage = _storage(tmp_path)
    _seed(
        storage,
        {"tokens": {"access_token": "stale", "token_type": "bearer", "expires_at": time.time() - 100}},
    )
    with pytest.raises(_bridge.ReauthenticationRequired, match="No refresh_token"):
        _run(SERVER, storage)


def test_missing_client_id_raises(tmp_path):
    storage = _storage(tmp_path)
    _seed(storage, _expired_entry(client_info={}))
    with pytest.raises(_bridge.ReauthenticationRequired, match="No client_id"):
        _run(SERVER, storage)


def test_missing_token_endpoint_raises(tmp_path):
    storage = _storage(tmp_path)
    entry = _expired_entry()
    del entry["token_endpoint"]
    _seed(storage, entry)
    with pytest.raises(_bridge.ReauthenticationRequired, match="token_endpoint"):
        _run(SERVER, storage)


# ---------------------------------------------------------------------------
# HTTP outcomes
# ---------------------------------------------------------------------------


def test_non_200_response_raises_with_status_and_body(tmp_path):
    storage = _storage(tmp_path)
    _seed(storage, _expired_entry())
    patcher, _ = _patch_httpx(status_code=400, text="invalid_grant")
    with patcher, pytest.raises(_bridge.ReauthenticationRequired, match="400"):
        _run(SERVER, storage)
    # Stale token must be left untouched on failure.
    assert storage._load()[SERVER]["tokens"]["access_token"] == "stale"


def test_success_persists_new_token(tmp_path):
    storage = _storage(tmp_path)
    _seed(storage, _expired_entry())
    capture: dict = {}
    patcher, _ = _patch_httpx(
        json_payload={
            "access_token": "new_tok",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "new_rt",
        },
        capture=capture,
    )
    with patcher:
        _run(SERVER, storage)

    saved = storage._load()[SERVER]["tokens"]
    assert saved["access_token"] == "new_tok"
    assert saved["refresh_token"] == "new_rt"
    # expires_in is converted to an absolute expires_at on save.
    assert saved["expires_at"] > time.time()

    assert capture["endpoint"] == "https://auth.example.com/token"
    assert capture["data"]["grant_type"] == "refresh_token"
    assert capture["data"]["refresh_token"] == "rt"
    assert capture["data"]["client_id"] == "cid"


def test_success_without_client_secret_omits_it(tmp_path):
    storage = _storage(tmp_path)
    _seed(storage, _expired_entry())
    capture: dict = {}
    patcher, _ = _patch_httpx(
        json_payload={"access_token": "n", "token_type": "bearer"}, capture=capture
    )
    with patcher:
        _run(SERVER, storage)
    assert "client_secret" not in capture["data"]


def test_success_with_client_secret_includes_it(tmp_path):
    storage = _storage(tmp_path)
    _seed(
        storage,
        _expired_entry(client_info={"client_id": "cid", "client_secret": "shh"}),
    )
    capture: dict = {}
    patcher, _ = _patch_httpx(
        json_payload={"access_token": "n", "token_type": "bearer"}, capture=capture
    )
    with patcher:
        _run(SERVER, storage)
    assert capture["data"]["client_secret"] == "shh"
