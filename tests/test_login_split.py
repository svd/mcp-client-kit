"""Two-step OAuth login: begin_login() / complete_login() against a fake MCP + OAuth server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from _fake_oauth import AS_URL, MCP_URL, FakeOAuth
from mcp.client.auth import OAuthClientProvider, OAuthRegistrationError

from mcpgen import _bridge, _login
from mcpgen._login import (
    AuthorizationDenied,
    CodeExchangeRejected,
    LoginNotCompleted,
    LoginStateMismatch,
    PendingLogin,
    PendingLoginExpired,
    begin_login,
    callback_state,
    cancel_login,
    complete_login,
)

REDIRECT = "https://staffing.example/_auth/callback"
PRIOR = {
    "client_info": {"client_id": "old-client", "redirect_uris": ["http://localhost:1234/callback"]},
    "tokens": {
        "access_token": "old-at",
        "token_type": "Bearer",
        "refresh_token": "old-rt",
        "expires_at": 4_000_000_000,
    },
    "token_endpoint": f"{AS_URL}/token",
}


@pytest.fixture(autouse=True)
def _isolated_server_config(tmp_path, monkeypatch):
    """`servers()` caches module-globally, and an explicit config_path fills that cache.

    Without a reset, a test that loads an `oauthClient` block leaks it into every later
    test that names the same server; the empty config also keeps the default search off
    the developer's real ~/.mcpgen/servers.json.
    """
    empty = tmp_path / "no-servers.json"
    empty.write_text("{}")
    monkeypatch.setenv("MCPGEN_SERVERS", str(empty))
    monkeypatch.setattr(_bridge, "_SERVERS_SEARCH", [])
    monkeypatch.setattr(_bridge, "_servers_cache", None)
    monkeypatch.setattr(_bridge, "_static_clients_cache", {})


@pytest.fixture
def fake(monkeypatch):
    oauth = FakeOAuth()
    monkeypatch.setattr(_login, "_oauth_client", oauth.client_factory())
    return oauth


@pytest.fixture
def creds(tmp_path):
    path = tmp_path / "credentials.json"
    _bridge.FileTokenStorage("acme", path)._save({"acme": PRIOR, "other": {"tokens": {"access_token": "x"}}})
    return path


def _store(path: Path) -> dict:
    return json.loads(path.read_text())


def _record(path: Path, server: str = "acme") -> dict:
    return _store(path)[server]["pending_login"]


def _begin(creds: Path, **kwargs) -> PendingLogin:
    kwargs.setdefault("redirect_uri", REDIRECT)
    return asyncio.run(begin_login("acme", url=MCP_URL, creds_path=creds, cred_backend="file", **kwargs))


def _complete(creds: Path, server: str, callback: str, **kwargs):
    kwargs.setdefault("verify", False)
    return asyncio.run(complete_login(server, callback, creds_path=creds, cred_backend="file", **kwargs))


# ── Happy path ───────────────────────────────────────────────────────────────


def test_begin_returns_an_authorization_url(fake, creds):
    pending = _begin(creds)

    query = parse_qs(urlparse(pending.authorization_url).query)
    assert pending.authorization_url.startswith(f"{AS_URL}/authorize?")
    assert query["redirect_uri"] == [REDIRECT]
    assert query["state"] == [pending.state]
    assert query["code_challenge_method"] == ["S256"]
    assert query["resource"] == [MCP_URL]
    assert fake.registrations[0]["redirect_uris"] == [REDIRECT]
    assert pending.server_name == "acme"
    assert pending.redirect_uri == REDIRECT
    assert not pending.expired


def test_begin_stores_the_record_and_keeps_the_tokens(fake, creds):
    pending = _begin(creds)

    entry = _store(creds)["acme"]
    assert entry["tokens"] == PRIOR["tokens"] and entry["client_info"] == PRIOR["client_info"]
    assert entry["token_endpoint"] == PRIOR["token_endpoint"]
    record = entry["pending_login"]
    assert record["format"] == 1 and record["state"] == pending.state
    assert record["redirect_uri"] == REDIRECT and record["code_verifier"]
    assert record["authorization_url"] == pending.authorization_url
    assert record["expires_at"] == pending.expires_at
    assert record["client_info"]["client_id"] == "client-1"
    assert _store(creds)["other"] == {"tokens": {"access_token": "x"}}
    assert oct(creds.stat().st_mode & 0o777) == "0o600"


def test_complete_writes_the_entry_login_writes_and_removes_the_record(fake, creds):
    pending = _begin(creds)

    result = _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    entry = _store(creds)["acme"]
    assert set(entry) == {"client_info", "tokens", "token_endpoint"}
    assert entry["client_info"]["client_id"] == "client-1"
    assert entry["client_info"]["redirect_uris"] == [REDIRECT]
    assert entry["tokens"]["access_token"] == "at-client-1"
    assert entry["tokens"]["refresh_token"] == "rt-client-1"
    assert isinstance(entry["tokens"]["expires_at"], int)
    assert entry["token_endpoint"] == f"{AS_URL}/token" == result.token_endpoint
    assert result.tools_count is None
    # Other servers' entries survive the swap.
    assert _store(creds)["other"] == {"tokens": {"access_token": "x"}}
    # The session path reads it back as a live, renewable credential.
    storage = _bridge.FileTokenStorage("acme", creds)
    token = asyncio.run(storage.get_tokens())
    assert token is not None and token.access_token == "at-client-1"
    assert _bridge._can_pre_flight_refresh(entry, entry["tokens"])


def test_complete_verify_runs_list_tools_over_the_saved_credential(fake, creds, monkeypatch):
    seen = {}

    @asynccontextmanager
    async def fake_session(server_name, server_url, *, cred_backend=None, creds_path=None, client_name=None):
        seen["entry"] = _store(creds_path)[server_name]
        seen["url"] = server_url

        async def list_tools():
            return SimpleNamespace(tools=[object(), object(), object()])

        yield SimpleNamespace(list_tools=list_tools)

    monkeypatch.setattr(_login, "_http_session", fake_session)
    pending = _begin(creds)

    result = _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url), verify=True)

    assert result.tools_count == 3
    assert seen["url"] == MCP_URL
    assert seen["entry"]["tokens"]["access_token"] == "at-client-1"


def test_complete_verify_failure_keeps_the_new_token(fake, creds, monkeypatch):
    @asynccontextmanager
    async def failing_session(*args, **kwargs):
        raise RuntimeError("502 from origin")
        yield  # pragma: no cover

    monkeypatch.setattr(_login, "_http_session", failing_session)
    pending = _begin(creds)

    with pytest.raises(_bridge.PostLoginCheckFailed, match="502 from origin"):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url), verify=True)

    assert _store(creds)["acme"]["tokens"]["access_token"] == "at-client-1"
    assert "pending_login" not in _store(creds)["acme"]


# ── Failures leave the previous credential in place ──────────────────────────


def _without_record(path: Path) -> dict:
    return {k: v for k, v in _store(path)["acme"].items() if k != "pending_login"}


def test_complete_without_a_pending_login_is_a_state_mismatch(fake, creds):
    with pytest.raises(LoginStateMismatch, match="no pending login"):
        _complete(creds, "acme", f"{REDIRECT}?code=abc&state=s1")

    assert _store(creds)["acme"] == PRIOR
    assert fake.token_requests == []


def test_mismatched_state_keeps_the_record(fake, creds):
    pending = _begin(creds)
    before = _record(creds)

    with pytest.raises(LoginStateMismatch):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url, state="someone-else"))

    assert _record(creds) == before
    assert _without_record(creds) == PRIOR
    assert fake.token_requests == []
    # The real callback still completes.
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))


def test_missing_state_raises_state_mismatch_and_keeps_the_record(fake, creds):
    pending = _begin(creds)
    callback = FakeOAuth.authorize(pending.authorization_url).split("&state=")[0]

    with pytest.raises(LoginStateMismatch, match="no state"):
        _complete(creds, "acme", callback)

    assert _record(creds)["state"] == pending.state
    assert fake.token_requests == []


def test_error_without_state_keeps_the_record(fake, creds):
    pending = _begin(creds)
    callback = f"{REDIRECT}?error=access_denied&error_description=User+declined"

    with pytest.raises(AuthorizationDenied) as info:
        _complete(creds, "acme", callback)

    assert info.value.error == "access_denied"
    assert _record(creds)["state"] == pending.state
    assert _without_record(creds) == PRIOR


def test_error_redirect_for_another_login_is_a_state_mismatch(fake, creds):
    pending = _begin(creds)

    with pytest.raises(LoginStateMismatch):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url, deny=True, state="other"))

    assert _record(creds)["state"] == pending.state


def test_access_denied_raises_typed_error_with_the_servers_detail(fake, creds):
    pending = _begin(creds)

    with pytest.raises(AuthorizationDenied) as info:
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url, deny=True))

    assert info.value.error == "access_denied"
    assert info.value.error_description == "User declined"
    assert _store(creds)["acme"] == PRIOR


def test_rejected_code_exchange_raises_typed_error(fake, creds):
    fake.token_error = (400, {"error": "invalid_grant", "error_description": "code already used"})
    pending = _begin(creds)

    with pytest.raises(CodeExchangeRejected) as info:
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert info.value.status_code == 400
    assert info.value.error == "invalid_grant"
    assert info.value.error_description == "code already used"
    assert _store(creds)["acme"] == PRIOR


def _expire(monkeypatch) -> None:
    real_time = _login.time.time
    monkeypatch.setattr(_login.time, "time", lambda: real_time() + 61)


@pytest.mark.parametrize("case", ["expired", "denied", "no_code", "iss", "exchange_rejected"])
def test_failure_after_the_state_matched_removes_the_record(fake, creds, monkeypatch, case):
    pending = _begin(creds, ttl=60)
    callback = FakeOAuth.authorize(pending.authorization_url, deny=case == "denied")
    expected: type[Exception] = {
        "expired": PendingLoginExpired,
        "denied": AuthorizationDenied,
        "no_code": LoginNotCompleted,
        "iss": LoginStateMismatch,
        "exchange_rejected": CodeExchangeRejected,
    }[case]
    if case == "expired":
        _expire(monkeypatch)
    elif case == "no_code":
        callback = f"{REDIRECT}?state={pending.state}"
    elif case == "iss":
        callback += "&iss=https%3A%2F%2Fevil.test"
    elif case == "exchange_rejected":
        fake.token_error = (400, {"error": "invalid_grant"})

    with pytest.raises(expected) as info:
        _complete(creds, "acme", callback)

    assert type(info.value) is expected
    assert _store(creds)["acme"] == PRIOR
    if case != "exchange_rejected":
        assert fake.token_requests == []
    # Used up: the same callback again finds no pending login.
    with pytest.raises(LoginStateMismatch, match="no pending login"):
        _complete(creds, "acme", callback)


def test_a_200_that_is_not_a_token_is_a_rejected_exchange(fake, creds):
    fake.token_error = (200, {"access_token": "leaky-secret", "token_type": "Bearer", "expires_in": "soon"})
    pending = _begin(creds)

    with pytest.raises(CodeExchangeRejected) as info:
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert "leaky-secret" not in str(info.value)
    assert info.value.__cause__ is None
    assert _store(creds)["acme"] == PRIOR


def test_wrong_verifier_is_rejected_by_the_token_endpoint(fake, creds):
    pending = _begin(creds)
    other = _begin(creds)

    with pytest.raises(CodeExchangeRejected, match="invalid_grant"):
        # A code bound to `pending`'s PKCE challenge, sent with the stored login's state.
        tampered = FakeOAuth.authorize(pending.authorization_url, state=other.state)
        _complete(creds, "acme", tampered)


def test_authorization_denied_is_a_value_error():
    assert issubclass(AuthorizationDenied, ValueError)
    assert issubclass(AuthorizationDenied, LoginNotCompleted)


# ── Single use and supersession ──────────────────────────────────────────────


def test_second_begin_supersedes_the_first(fake, creds):
    first = _begin(creds)
    second = _begin(creds)

    with pytest.raises(LoginStateMismatch):
        _complete(creds, first.server_name, FakeOAuth.authorize(first.authorization_url))

    assert _record(creds)["state"] == second.state
    client_id = _record(creds)["client_info"]["client_id"]
    _complete(creds, "acme", FakeOAuth.authorize(second.authorization_url))
    assert _store(creds)["acme"]["tokens"]["access_token"] == f"at-{client_id}"


def test_replayed_callback_after_success_is_refused(fake, creds):
    pending = _begin(creds)
    callback = FakeOAuth.authorize(pending.authorization_url)
    _complete(creds, "acme", callback)
    saved = _store(creds)["acme"]

    with pytest.raises(LoginStateMismatch):
        _complete(creds, "acme", callback)

    assert len(fake.token_requests) == 1
    assert _store(creds)["acme"] == saved


def test_two_concurrent_completes_make_one_exchange(fake, creds):
    pending = _begin(creds)
    callback = FakeOAuth.authorize(pending.authorization_url)

    async def run():
        return await asyncio.gather(
            complete_login("acme", callback, creds_path=creds, cred_backend="file", verify=False),
            complete_login("acme", callback, creds_path=creds, cred_backend="file", verify=False),
            return_exceptions=True,
        )

    outcomes = asyncio.run(run())

    assert sum(isinstance(o, _login.LoginResult) for o in outcomes) == 1
    assert sum(isinstance(o, LoginStateMismatch) for o in outcomes) == 1
    assert len(fake.token_requests) == 1
    assert _store(creds)["acme"]["tokens"]["access_token"] == "at-client-1"


def test_refresh_between_begin_and_complete_keeps_the_record(fake, creds):
    from mcp.shared.auth import OAuthToken

    pending = _begin(creds)
    storage = _bridge.FileTokenStorage("acme", creds)
    asyncio.run(storage.set_tokens(OAuthToken(access_token="refreshed", token_type="Bearer", expires_in=3600)))

    entry = _store(creds)["acme"]
    assert entry["tokens"]["access_token"] == "refreshed"
    assert entry["pending_login"]["state"] == pending.state
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))
    assert _store(creds)["acme"]["tokens"]["access_token"] == "at-client-1"


def test_begin_during_the_exchange_survives_the_final_write(fake, creds, monkeypatch):
    first = _begin(creds)
    newer: dict[str, PendingLogin] = {}
    real = OAuthClientProvider._handle_token_response

    async def begin_then_handle(self, response):
        newer["p"] = await begin_login(
            "acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="file"
        )
        return await real(self, response)

    monkeypatch.setattr(OAuthClientProvider, "_handle_token_response", begin_then_handle)
    _complete(creds, "acme", FakeOAuth.authorize(first.authorization_url))
    monkeypatch.setattr(OAuthClientProvider, "_handle_token_response", real)

    entry = _store(creds)["acme"]
    assert entry["tokens"]["access_token"] == "at-client-1"
    assert entry["pending_login"]["state"] == newer["p"].state
    _complete(creds, "acme", FakeOAuth.authorize(newer["p"].authorization_url))
    assert "pending_login" not in _store(creds)["acme"]


# ── Store edge cases ─────────────────────────────────────────────────────────


def test_begin_quarantines_a_corrupt_store(fake, tmp_path):
    creds = tmp_path / "credentials.json"
    creds.write_text("{not json")
    creds.chmod(0o600)

    pending = _begin(creds)

    assert list(tmp_path.glob("credentials.json.corrupt.*"))
    assert set(_store(creds)) == {"acme"}
    assert _record(creds)["state"] == pending.state


def test_begin_on_a_server_with_no_entry_creates_a_pending_only_entry(fake, tmp_path):
    creds = tmp_path / "credentials.json"

    pending = _begin(creds)

    assert set(_store(creds)) == {"acme"}
    assert set(_store(creds)["acme"]) == {"pending_login"}
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))
    assert set(_store(creds)["acme"]) == {"client_info", "tokens", "token_endpoint"}


def _put_record(creds: Path, record: dict) -> None:
    store = _store(creds)
    store["acme"]["pending_login"] = record
    _bridge.FileTokenStorage("acme", creds)._save(store)


def test_unknown_record_format_is_no_pending_login(fake, creds):
    pending = _begin(creds)
    _put_record(creds, {**_record(creds), "format": 2})

    with pytest.raises(LoginStateMismatch, match="no pending login"):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))
    assert fake.token_requests == []


def test_a_record_missing_a_field_is_no_pending_login(fake, creds):
    pending = _begin(creds)
    record = _record(creds)
    del record["code_verifier"]
    _put_record(creds, record)

    with pytest.raises(LoginStateMismatch, match="no pending login"):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))


def test_unusable_record_is_no_pending_login(fake, creds):
    pending = _begin(creds)
    _put_record(creds, {**_record(creds), "client_info": {"client_secret": "shh", "redirect_uris": "bad"}})

    with pytest.raises(LoginStateMismatch, match="no pending login") as info:
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    chain = [info.value, info.value.__context__, info.value.__cause__]
    assert all("shh" not in str(e) for e in chain if e is not None)
    assert info.value.__context__ is None and info.value.__cause__ is None
    assert fake.token_requests == []


def test_complete_swaps_a_corrupt_store_aside_for_the_final_write(fake, creds, monkeypatch):
    pending = _begin(creds)
    real = OAuthClientProvider._handle_token_response

    async def corrupt_then_handle(self, response):
        creds.write_text("{not json")
        return await real(self, response)

    monkeypatch.setattr(OAuthClientProvider, "_handle_token_response", corrupt_then_handle)
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert _store(creds)["acme"]["tokens"]["access_token"] == "at-client-1"
    assert list(creds.parent.glob("credentials.json.corrupt.*"))


def test_complete_on_a_corrupt_store_leaves_it_in_place(fake, creds):
    pending = _begin(creds)
    creds.write_text("{not json")

    with pytest.raises(LoginNotCompleted, match="not a readable credential store") as info:
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert type(info.value) is LoginNotCompleted
    assert creds.read_text() == "{not json"
    assert not list(creds.parent.glob("credentials.json.corrupt.*"))
    assert fake.token_requests == []


def _fake_ring(tmp_path: Path, monkeypatch) -> dict[str, str]:
    """An in-memory OS keyring: the store lives under `ring["store"]` while it exists."""
    ring: dict[str, str] = {}
    monkeypatch.setattr(_bridge, "_KEYRING_LOCK_PATH", tmp_path / "keyring")
    monkeypatch.setattr(_bridge, "_keyring_read_raw", lambda: json.loads(ring.get("store", "{}")))
    monkeypatch.setattr(_bridge, "_keyring_write_raw", lambda data: ring.__setitem__("store", json.dumps(data)))
    monkeypatch.setattr(_bridge, "_keyring_clear_raw", lambda: ring.pop("store", None))
    return ring


def _refuse_keyring_writes(monkeypatch) -> None:
    def broken(*_args):
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(_bridge, "_keyring_write_raw", broken)
    monkeypatch.setattr(_bridge, "_keyring_clear_raw", broken)


@pytest.mark.parametrize("others", [False, True], ids=["clear", "save"])
def test_keyring_fallback_during_the_take_refuses_the_exchange(fake, tmp_path, monkeypatch, others):
    creds = tmp_path / "credentials.json"
    ring = _fake_ring(tmp_path, monkeypatch)
    if others:
        _bridge._keyring_write_raw({"other": {"tokens": {"access_token": "x"}}})
    pending = asyncio.run(
        begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="keyring")
    )
    assert json.loads(ring["store"])["acme"]["pending_login"]["state"] == pending.state

    _refuse_keyring_writes(monkeypatch)
    with pytest.raises(LoginNotCompleted, match="keychain refused") as info:
        asyncio.run(
            complete_login(
                "acme",
                FakeOAuth.authorize(pending.authorization_url),
                creds_path=creds,
                cred_backend="keyring",
                verify=False,
            )
        )

    assert type(info.value) is LoginNotCompleted
    assert fake.token_requests == []
    assert json.loads(ring["store"])["acme"]["pending_login"]["state"] == pending.state
    assert not creds.exists()


def test_begin_refused_by_the_keyring_raises_and_writes_nothing(fake, tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    ring = _fake_ring(tmp_path, monkeypatch)
    _bridge._keyring_write_raw({"other": {"tokens": {"access_token": "x"}}})
    _refuse_keyring_writes(monkeypatch)

    with pytest.raises(RuntimeError, match="keychain refused"):
        asyncio.run(begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="keyring"))

    assert json.loads(ring["store"]) == {"other": {"tokens": {"access_token": "x"}}}
    assert not creds.exists()


def test_complete_whose_save_the_keyring_refuses_keeps_the_previous_credential(fake, tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    ring = _fake_ring(tmp_path, monkeypatch)
    _bridge._keyring_write_raw({"acme": PRIOR, "other": {"tokens": {"access_token": "x"}}})
    pending = asyncio.run(
        begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="keyring")
    )
    writes = {"n": 0}
    real_write = _bridge._keyring_write_raw

    def refuse_after_the_take(data):
        writes["n"] += 1
        if writes["n"] > 1:
            raise RuntimeError("keychain locked")
        real_write(data)

    monkeypatch.setattr(_bridge, "_keyring_write_raw", refuse_after_the_take)

    with pytest.raises(LoginNotCompleted, match="keychain refused") as info:
        asyncio.run(
            complete_login(
                "acme",
                FakeOAuth.authorize(pending.authorization_url),
                creds_path=creds,
                cred_backend="keyring",
                verify=True,
            )
        )

    assert type(info.value) is LoginNotCompleted
    assert len(fake.token_requests) == 1
    assert json.loads(ring["store"]) == {"acme": PRIOR, "other": {"tokens": {"access_token": "x"}}}
    assert not creds.exists()


def test_a_keyring_read_error_before_the_round_does_not_send_the_record_to_the_file(fake, tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    ring: dict[str, str] = {}
    reads = {"n": 0}

    def flaky_read():
        reads["n"] += 1
        if reads["n"] == 1:
            raise RuntimeError("keychain busy")
        return json.loads(ring.get("store", "{}"))

    monkeypatch.setattr(_bridge, "_KEYRING_LOCK_PATH", tmp_path / "keyring")
    monkeypatch.setattr(_bridge, "_keyring_read_raw", flaky_read)
    monkeypatch.setattr(_bridge, "_keyring_write_raw", lambda data: ring.__setitem__("store", json.dumps(data)))

    with pytest.warns(UserWarning, match="keyring unusable"):
        pending = asyncio.run(
            begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="keyring")
        )

    assert json.loads(ring["store"])["acme"]["pending_login"]["state"] == pending.state
    assert not creds.exists()


@pytest.mark.parametrize("entry", [None, "x", []])
def test_begin_replaces_an_entry_that_is_not_an_object(fake, tmp_path, entry):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"acme": entry, "other": {"tokens": {"access_token": "x"}}}))

    pending = _begin(creds)

    assert _record(creds)["state"] == pending.state
    assert _store(creds)["other"] == {"tokens": {"access_token": "x"}}


def test_two_processes_taking_the_same_record_serialize_on_the_store_lock(fake, creds, monkeypatch):
    """Threads stand in for processes: each take loads, pauses, then saves.

    Without the lock both would load the record before either saved its removal.
    """
    import threading
    import time as _time

    pending = _begin(creds)
    callback = FakeOAuth.authorize(pending.authorization_url)
    real_load = _login._load_store_for_login

    def slow_load(storage):
        data = real_load(storage)
        _time.sleep(0.3)
        return data

    monkeypatch.setattr(_login, "_load_store_for_login", slow_load)
    outcomes: list[object] = []

    def take():
        storage = _bridge.FileTokenStorage("acme", creds, backend="file")
        try:
            outcomes.append(_login._take_pending(storage, "acme", callback))
        except LoginStateMismatch as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=take) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(isinstance(o, _login._PendingRecord) for o in outcomes) == 1
    assert sum(isinstance(o, LoginStateMismatch) for o in outcomes) == 1


def test_a_take_that_empties_the_store_removes_the_file(fake, tmp_path, monkeypatch):
    creds = tmp_path / "credentials.json"
    pending = _begin(creds, ttl=60)
    _expire(monkeypatch)

    with pytest.raises(PendingLoginExpired):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert not creds.exists()


# ── Cancel ───────────────────────────────────────────────────────────────────


def _cancel(creds: Path, server: str = "acme", backend: str = "file") -> bool:
    return asyncio.run(cancel_login(server, creds_path=creds, cred_backend=backend))


def test_cancel_removes_only_the_record(fake, creds):
    pending = _begin(creds)

    assert _cancel(creds) is True

    entry = _store(creds)["acme"]
    assert "pending_login" not in entry and entry == PRIOR
    assert _store(creds)["other"] == {"tokens": {"access_token": "x"}}
    with pytest.raises(LoginStateMismatch, match="no pending login"):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))


def test_cancel_without_a_pending_login_returns_false(fake, creds):
    before = creds.read_text()

    assert _cancel(creds) is False
    assert _cancel(creds, "nobody") is False

    assert creds.read_text() == before


def test_cancel_without_a_store_returns_false(fake, tmp_path):
    creds = tmp_path / "credentials.json"

    assert _cancel(creds) is False
    assert not creds.exists()


@pytest.mark.parametrize("entry", [None, "x", []])
def test_cancel_on_an_entry_that_is_not_an_object_returns_false(fake, tmp_path, entry):
    creds = tmp_path / "credentials.json"
    _bridge.FileTokenStorage("acme", creds)._save({"acme": entry})

    assert _cancel(creds) is False
    assert _store(creds) == {"acme": entry}


def test_cancel_drops_a_pending_only_entry_and_clears_an_empty_store(fake, tmp_path):
    creds = tmp_path / "credentials.json"
    _begin(creds)

    assert _cancel(creds) is True

    assert not creds.exists()


def test_cancel_drops_a_pending_only_entry_and_keeps_the_others(fake, tmp_path):
    creds = tmp_path / "credentials.json"
    _bridge.FileTokenStorage("acme", creds)._save({"other": {"tokens": {"access_token": "x"}}})
    _begin(creds)

    assert _cancel(creds) is True

    assert _store(creds) == {"other": {"tokens": {"access_token": "x"}}}


def test_cancel_removes_an_unusable_record(fake, creds):
    _begin(creds)
    _put_record(creds, {**_record(creds), "format": 2, "client_info": {"client_secret": "shh"}})

    assert _cancel(creds) is True

    assert _store(creds)["acme"] == PRIOR
    assert "shh" not in creds.read_text()


@pytest.mark.parametrize("others", [False, True], ids=["pending-only", "with-others"])
def test_cancel_on_a_keyring_store(fake, tmp_path, monkeypatch, others):
    creds = tmp_path / "credentials.json"
    ring = _fake_ring(tmp_path, monkeypatch)
    if others:
        _bridge._keyring_write_raw({"other": {"tokens": {"access_token": "x"}}})
    asyncio.run(begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="keyring"))

    assert _cancel(creds, backend="keyring") is True

    if others:
        assert json.loads(ring["store"]) == {"other": {"tokens": {"access_token": "x"}}}
    else:
        assert "store" not in ring
    assert not creds.exists()


@pytest.mark.parametrize("others", [False, True], ids=["clear", "save"])
def test_cancel_refused_by_the_keyring_raises_and_keeps_the_record(fake, tmp_path, monkeypatch, others):
    creds = tmp_path / "credentials.json"
    ring = _fake_ring(tmp_path, monkeypatch)
    if others:
        _bridge._keyring_write_raw({"other": {"tokens": {"access_token": "x"}}})
    pending = asyncio.run(
        begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=creds, cred_backend="keyring")
    )
    _refuse_keyring_writes(monkeypatch)

    with pytest.raises(RuntimeError, match="still there"):
        _cancel(creds, backend="keyring")

    assert json.loads(ring["store"])["acme"]["pending_login"]["state"] == pending.state
    assert not creds.exists()


def test_cancel_on_a_corrupt_store_leaves_it_in_place(fake, creds):
    creds.write_text("{not json")

    with pytest.raises(RuntimeError, match="not a readable credential store"):
        _cancel(creds)

    assert creds.read_text() == "{not json"
    assert not list(creds.parent.glob("credentials.json.corrupt.*"))


def test_cancel_is_exported():
    import mcpgen

    assert mcpgen.cancel_login is cancel_login
    assert "cancel_login" in mcpgen.__all__


def test_delete_cred_removes_a_pending_only_entry(fake, tmp_path):
    creds = tmp_path / "credentials.json"
    _begin(creds)

    assert _bridge.delete_cred("acme", backend="file", credentials_path=creds) is True

    assert not creds.exists()


# ── Client registration reuse ────────────────────────────────────────────────


def test_second_begin_with_the_same_redirect_uri_reuses_the_registration(fake, creds):
    first = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(first.authorization_url))

    second = _begin(creds)

    assert len(fake.registrations) == 1
    assert _record(creds)["client_info"]["client_id"] == "client-1"
    assert parse_qs(urlparse(second.authorization_url).query)["client_id"] == ["client-1"]


def test_a_different_redirect_uri_registers_anew(fake, creds):
    first = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(first.authorization_url))

    _begin(creds, redirect_uri="https://staffing.example:8443/other/callback")

    assert len(fake.registrations) == 2
    assert _record(creds)["client_info"]["client_id"] == "client-2"


def test_reuse_can_be_turned_off(fake, creds):
    first = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(first.authorization_url))

    _begin(creds, reuse_registration=False)

    assert len(fake.registrations) == 2


def test_a_registration_from_a_moved_authorization_server_is_not_reused(fake, creds):
    first = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(first.authorization_url))
    fake.as_url = "https://as2.test"

    second = _begin(creds)

    assert len(fake.registrations) == 2
    assert _record(creds)["client_info"]["client_id"] == "client-2"
    assert second.authorization_url.startswith("https://as2.test/authorize?")


def test_second_begin_reuses_the_client_the_first_registered(fake, creds):
    _begin(creds)
    first = _record(creds)

    second = _begin(creds)

    assert len(fake.registrations) == 1
    assert first["client_info"]["client_id"] == "client-1"
    assert _record(creds)["client_info"]["client_id"] == "client-1"
    assert parse_qs(urlparse(second.authorization_url).query)["client_id"] == ["client-1"]


def test_a_pending_registration_is_checked_against_its_own_token_endpoint(fake, creds):
    _begin(creds)
    store = _store(creds)
    store["acme"]["token_endpoint"] = "https://retired-as.test/token"
    _bridge.FileTokenStorage("acme", creds)._save(store)

    _begin(creds)

    assert len(fake.registrations) == 1
    assert _record(creds)["client_info"]["client_id"] == "client-1"


def test_a_pending_registration_from_a_moved_authorization_server_is_not_reused(fake, creds):
    _begin(creds)
    store = _store(creds)
    del store["acme"]["token_endpoint"]  # only the record's metadata can tell the AS moved
    _bridge.FileTokenStorage("acme", creds)._save(store)
    fake.as_url = "https://as2.test"

    second = _begin(creds)

    assert len(fake.registrations) == 2
    assert _record(creds)["client_info"]["client_id"] == "client-2"
    assert second.authorization_url.startswith("https://as2.test/authorize?")


def test_a_pending_registration_without_a_known_token_endpoint_is_not_reused(fake, creds):
    _begin(creds)
    store = _store(creds)
    del store["acme"]["token_endpoint"]
    store["acme"]["pending_login"]["oauth_metadata"] = None  # the AS published no metadata
    _bridge.FileTokenStorage("acme", creds)._save(store)

    _begin(creds)

    assert len(fake.registrations) == 2


def test_an_unusable_pending_record_offers_no_registration(fake, creds):
    store = _store(creds)
    store["acme"]["pending_login"] = {
        "format": 1,
        "client_info": {"client_id": "stale", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"},
    }
    _bridge.FileTokenStorage("acme", creds)._save(store)

    _begin(creds)

    assert len(fake.registrations) == 1
    assert _record(creds)["client_info"]["client_id"] == "client-1"


# ── Concurrency ──────────────────────────────────────────────────────────────


def test_two_pending_logins_against_two_stores_complete_independently(fake, tmp_path):
    alice, bob = tmp_path / "alice.json", tmp_path / "bob.json"

    async def run() -> None:
        pa, pb = await asyncio.gather(
            begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=alice, cred_backend="file"),
            begin_login("acme", redirect_uri=REDIRECT, url=MCP_URL, creds_path=bob, cred_backend="file"),
        )
        assert pa.state != pb.state
        # Completed in the opposite order, each routed by the state its callback carries.
        store_for = {pa.state: alice, pb.state: bob}
        callbacks = [FakeOAuth.authorize(pb.authorization_url), FakeOAuth.authorize(pa.authorization_url)]
        await asyncio.gather(
            *(
                complete_login("acme", cb, creds_path=store_for[callback_state(cb)], cred_backend="file", verify=False)
                for cb in callbacks
            )
        )

    asyncio.run(run())

    a, b = _store(alice)["acme"], _store(bob)["acme"]
    assert {a["client_info"]["client_id"], b["client_info"]["client_id"]} == {"client-1", "client-2"}
    assert a["tokens"]["access_token"] == f"at-{a['client_info']['client_id']}"
    assert b["tokens"]["access_token"] == f"at-{b['client_info']['client_id']}"


def test_begin_and_complete_in_two_processes(tmp_path):
    creds = tmp_path / "credentials.json"
    tests_dir = Path(__file__).parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tests_dir), os.environ.get("PYTHONPATH", "")])}
    prelude = textwrap.dedent(
        """
        import asyncio, sys
        from _fake_oauth import FakeOAuth, MCP_URL
        from mcpgen import _login
        _login._oauth_client = FakeOAuth().client_factory()
        """
    )
    begin = prelude + textwrap.dedent(
        f"""
        p = asyncio.run(_login.begin_login("acme", redirect_uri={REDIRECT!r}, url=MCP_URL,
                                           creds_path={str(creds)!r}, cred_backend="file"))
        sys.stdout.write(p.authorization_url)
        """
    )
    # The second process gets only the server name and the callback URL.
    complete = prelude + textwrap.dedent(
        f"""
        callback = FakeOAuth.authorize(sys.stdin.read())
        r = asyncio.run(_login.complete_login("acme", callback, creds_path={str(creds)!r}, cred_backend="file",
                                              verify=False))
        print(r.token_endpoint)
        """
    )

    a = subprocess.run([sys.executable, "-c", begin], capture_output=True, text=True, env=env, check=True)
    b = subprocess.run([sys.executable, "-c", complete], input=a.stdout, capture_output=True, text=True, env=env)

    assert b.returncode == 0, b.stderr
    assert b.stdout.strip() == f"{AS_URL}/token"
    entry = _store(creds)["acme"]
    assert entry["tokens"]["access_token"] == "at-client-1"
    assert "pending_login" not in entry


# ── Secrets ──────────────────────────────────────────────────────────────────


def test_the_pending_login_carries_no_verifier_or_client_secret(fake, creds):
    pending = _begin(creds, client=STATIC)
    record = _record(creds)
    assert record["client_info"]["client_secret"] == "static-shh"

    for text in (repr(pending), str(pending), f"{pending}"):
        assert record["code_verifier"] not in text
        assert "static-shh" not in text
    assert not hasattr(pending, "code_verifier")
    assert not hasattr(pending, "client_info")
    assert not hasattr(pending, "to_json")
    assert "static-shh" not in repr(_login._PendingRecord.from_dict(record))


def test_no_secret_or_code_reaches_the_log(fake, creds, caplog):
    caplog.set_level(logging.DEBUG)
    pending = _begin(creds)
    verifier = _record(creds)["code_verifier"]
    callback = FakeOAuth.authorize(pending.authorization_url)
    code = parse_qs(urlparse(callback).query)["code"][0]

    _complete(creds, "acme", callback)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (verifier, code, "at-client-1", "rt-client-1", callback):
        assert secret not in logged


def test_begin_logs_no_oauth_flow_error(fake, creds, caplog):
    caplog.set_level(logging.DEBUG, logger="mcp.client.auth")
    _begin(creds)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── Input validation and edge cases ──────────────────────────────────────────


@pytest.mark.parametrize("uri", ["/relative/callback", "callback", "https://h.example/cb#frag"])
def test_begin_rejects_a_redirect_uri_that_is_not_absolute(fake, creds, uri):
    with pytest.raises(ValueError, match="redirect_uri"):
        _begin(creds, redirect_uri=uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://staffing.example/cb",
        "http://10.0.0.5:8080/cb",
        "http://localhost.evil.example/cb",
        "ftp://h.example/cb",
        "com.example.app:/cb",
    ],
)
def test_begin_refuses_a_redirect_uri_without_tls(fake, creds, uri):
    with pytest.raises(ValueError, match="redirect_uri"):
        _begin(creds, redirect_uri=uri)
    assert fake.registrations == [] and fake.token_requests == []


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:8080/cb",
        "http://LOCALHOST/cb",
        "http://127.0.0.1/cb",
        "http://127.8.9.10:1/cb",
        "http://[::1]:9/cb",
        "https://h.example/cb",
    ],
)
def test_begin_accepts_https_and_loopback_http(fake, creds, uri):
    pending = _begin(creds, redirect_uri=uri)
    assert parse_qs(urlparse(pending.authorization_url).query)["redirect_uri"]


def test_a_static_client_registered_uri_is_held_to_the_same_rule(fake, creds):
    client = _login.StaticClient(client_id="c", redirect_uris=("http://intranet.example/cb",))
    with pytest.raises(ValueError, match="redirect_uri"):
        _begin(creds, client=client, redirect_uri="http://intranet.example/cb")


@pytest.mark.parametrize("ttl", [0, -5, float("nan"), float("inf")])
def test_begin_rejects_a_ttl_that_is_not_a_positive_finite_number(fake, creds, ttl):
    with pytest.raises(ValueError, match="ttl"):
        _begin(creds, ttl=ttl)
    assert "pending_login" not in _store(creds)["acme"]


@pytest.mark.parametrize("expires_at", [float("nan"), float("inf")])
def test_a_record_whose_expiry_is_not_finite_is_unusable(fake, creds, expires_at):
    _begin(creds)

    assert _login._PendingRecord.from_dict({**_record(creds), "expires_at": expires_at}) is None


def test_begin_defaults_to_the_callback_timeout(fake, creds):
    import time

    pending = _begin(creds)
    assert pending.expires_at - time.time() == pytest.approx(_bridge._CALLBACK_TIMEOUT, abs=5)


def test_begin_against_a_server_without_auth_says_so(fake, creds):
    fake.require_auth = False
    with pytest.raises(_bridge.LoginWontHelp, match="without asking for OAuth"):
        _begin(creds)


def test_begin_surfaces_a_refused_registration(fake, creds, monkeypatch):
    real = fake.handle

    def refuse(request):
        if request.url.path == "/register":
            import httpx

            return httpx.Response(400, json={"error": "invalid_redirect_uri"})
        return real(request)

    before = creds.read_bytes()
    monkeypatch.setattr(fake, "handle", refuse)
    with pytest.raises(OAuthRegistrationError, match="invalid_redirect_uri"):
        _begin(creds)
    assert creds.read_bytes() == before


def test_unknown_server_without_url(fake, creds, tmp_path):
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps({"mcpServers": {}}))
    with pytest.raises(ValueError, match="Unknown server"):
        asyncio.run(begin_login("nope", redirect_uri=REDIRECT, creds_path=creds, config_path=cfg))


def test_callback_state_reads_the_state_parameter():
    assert callback_state(f"{REDIRECT}?code=abc&state=s1") == "s1"
    assert callback_state(REDIRECT) is None


# ── SDK pins ─────────────────────────────────────────────────────────────────


def test_sdk_members_the_two_step_flow_uses_still_exist():
    for name in ("_perform_authorization", "_exchange_token_authorization_code", "_handle_token_response"):
        assert callable(getattr(OAuthClientProvider, name)), name


def test_authorization_url_matches_the_sdks_own(fake, creds):
    """`_BeginProvider` re-implements the SDK's URL; pin that both emit the same parameters."""
    pending = _begin(creds)
    ours = parse_qs(urlparse(pending.authorization_url).query)

    provider = _login._restore_provider(_login._PendingRecord.from_dict(_record(creds)), _login._StagingStorage())
    captured = {}

    async def redirect(url):
        captured["url"] = url

    async def callback():
        raise asyncio.CancelledError

    provider.context.redirect_handler = redirect
    provider.context.callback_handler = callback
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider._perform_authorization_code_grant())

    sdk_url = urlparse(captured["url"])
    sdk = parse_qs(sdk_url.query)
    assert f"{sdk_url.scheme}://{sdk_url.netloc}{sdk_url.path}" == pending.authorization_url.split("?")[0]
    assert set(sdk) == set(ours)
    for key in ("response_type", "client_id", "redirect_uri", "code_challenge_method", "resource"):
        assert sdk[key] == ours[key]


# ── Review fixes ─────────────────────────────────────────────────────────────


def test_a_confidential_registration_is_not_reused(fake, creds):
    store = _store(creds)
    store["acme"]["client_info"] = {
        "client_id": "legacy",
        "client_secret": "s",
        "token_endpoint_auth_method": "client_secret_basic",
        "redirect_uris": [REDIRECT],
    }
    _bridge.FileTokenStorage("acme", creds)._save(store)

    _begin(creds)

    assert len(fake.registrations) == 1
    assert _record(creds)["client_info"]["client_id"] == "client-1"


def test_a_registration_past_its_secret_expiry_is_not_reused(fake, creds):
    first = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(first.authorization_url))
    store = _store(creds)
    store["acme"]["client_info"]["client_secret_expires_at"] = 1
    _bridge.FileTokenStorage("acme", creds)._save(store)

    _begin(creds)

    assert len(fake.registrations) == 2


def test_every_request_carries_a_timeout(fake, creds):
    pending = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert fake.seen
    for request in fake.seen:
        assert request.extensions.get("timeout"), request.url


def test_a_callback_from_another_issuer_is_refused(fake, creds):
    pending = _begin(creds)
    callback = FakeOAuth.authorize(pending.authorization_url) + "&iss=https%3A%2F%2Fevil.test"

    with pytest.raises(LoginStateMismatch, match="issuer"):
        _complete(creds, "acme", callback)
    assert fake.token_requests == []


def test_a_callback_from_the_right_issuer_completes(fake, creds):
    pending = _begin(creds)
    callback = FakeOAuth.authorize(pending.authorization_url) + "&iss=https%3A%2F%2Fas.test"

    _complete(creds, "acme", callback)

    assert _store(creds)["acme"]["tokens"]["access_token"] == "at-client-1"


# ── Pre-registered (static) client ───────────────────────────────────────────

STATIC = _login.StaticClient(client_id="static-app", client_secret="static-shh", redirect_uris=(REDIRECT,))


def test_a_static_client_skips_registration_and_authenticates_in_the_body(fake, creds):
    pending = _begin(creds, client=STATIC)

    assert fake.registrations == []
    assert parse_qs(urlparse(pending.authorization_url).query)["client_id"] == ["static-app"]
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert fake.token_requests[-1]["client_secret"] == "static-shh"
    entry = _store(creds)["acme"]
    assert entry["client_info"]["client_id"] == "static-app"
    assert entry["client_info"]["client_secret"] == "static-shh"
    assert entry["client_info"]["token_endpoint_auth_method"] == "client_secret_post"
    assert entry["tokens"]["access_token"] == "at-static-app"


def test_a_public_static_client_sends_no_secret(fake, creds):
    pending = _begin(creds, client=_login.StaticClient(client_id="pub"))
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert "client_secret" not in fake.token_requests[-1]
    assert _store(creds)["acme"]["client_info"]["token_endpoint_auth_method"] == "none"


def test_a_static_client_refuses_an_unregistered_redirect_uri(fake, creds):
    with pytest.raises(ValueError, match="not one the static client"):
        _begin(creds, client=STATIC, redirect_uri="https://elsewhere.example/cb")
    assert fake.seen == []


@pytest.mark.parametrize(
    "client",
    [
        _login.StaticClient(client_id="c", client_secret="s", token_endpoint_auth_method="client_secret_basic"),
        _login.StaticClient(client_id="c", token_endpoint_auth_method="client_secret_post"),
        _login.StaticClient(client_id=""),
    ],
)
def test_a_static_client_that_cannot_refresh_is_refused(fake, creds, client):
    with pytest.raises(ValueError):
        _begin(creds, client=client)


def test_static_client_repr_hides_the_secret():
    assert "static-shh" not in repr(STATIC)
    assert "static-shh" not in str(STATIC)


def test_a_static_client_from_config(fake, creds, tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_SECRET", "from-env")
    cfg = tmp_path / "servers.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "acme": {
                        "type": "http",
                        "url": MCP_URL,
                        "oauthClient": {
                            "clientId": "cfg-app",
                            "clientSecret": "${ACME_SECRET}",
                            "redirectUris": [REDIRECT],
                        },
                    }
                }
            }
        )
    )

    pending = asyncio.run(begin_login("acme", redirect_uri=REDIRECT, creds_path=creds, config_path=cfg))
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert fake.registrations == []
    assert fake.token_requests[-1]["client_id"] == "cfg-app"
    assert fake.token_requests[-1]["client_secret"] == "from-env"


def test_parse_static_clients_accepts_snake_case_and_drops_blocks_without_an_id():
    raw = {
        "mcpServers": {
            "a": {"url": "u", "oauthClient": {"client_id": "x", "redirect_uris": ["https://h/cb"]}},
            "b": {"url": "u", "oauthClient": {"clientSecret": "only-secret"}},
            "c": {"url": "u"},
        }
    }
    assert _bridge._parse_static_clients(raw) == {
        "a": {
            "client_id": "x",
            "client_secret": None,
            "redirect_uris": ["https://h/cb"],
            "token_endpoint_auth_method": None,
            "issuer": None,
        }
    }


def test_parse_static_clients_expands_only_braced_variables(monkeypatch):
    monkeypatch.setenv("ACME_SECRET", "from-env")
    monkeypatch.setenv("HOME", "/home/someone")
    raw = {
        "acme": {
            "url": "u",
            "oauthClient": {
                "clientId": "app",
                "clientSecret": "a$HOME%HOME%${ACME_SECRET}$",
                "redirectUris": ["https://h.example/$HOME/${ACME_SECRET}"],
            },
        }
    }

    client = _bridge._parse_static_clients(raw)["acme"]

    assert client["client_secret"] == "a$HOME%HOME%from-env$"
    assert client["redirect_uris"] == ["https://h.example/$HOME/from-env"]


@pytest.mark.parametrize("uris", ["https://staffing.example/_auth/callback", [5], [REDIRECT, None], {"a": REDIRECT}])
def test_configured_redirect_uris_that_are_not_a_list_of_strings_are_refused(fake, creds, tmp_path, uris):
    cfg = _cfg(tmp_path, {"clientId": "cfg-app", "redirectUris": uris})

    with pytest.raises(ValueError, match="redirectUris must be a list of strings"):
        asyncio.run(begin_login("acme", redirect_uri=REDIRECT, creds_path=creds, config_path=cfg))
    assert fake.seen == []


def test_begin_reads_an_explicit_config_once(fake, creds, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, {"clientId": "cfg-app", "redirectUris": [REDIRECT]})
    reads = {"n": 0}
    real_parse = _bridge._parse_servers

    def counting_parse(raw):
        reads["n"] += 1
        return real_parse(raw)

    monkeypatch.setattr(_bridge, "_parse_servers", counting_parse)

    asyncio.run(begin_login("acme", redirect_uri=REDIRECT, creds_path=creds, config_path=cfg))

    assert reads["n"] == 1
    assert _record(creds)["client_info"]["client_id"] == "cfg-app"


def _cfg(tmp_path, client: dict) -> Path:
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps({"mcpServers": {"acme": {"type": "http", "url": MCP_URL, "oauthClient": client}}}))
    return cfg


def test_a_configured_client_is_not_used_for_an_overridden_url(fake, creds, tmp_path):
    cfg = _cfg(tmp_path, {"clientId": "cfg-app", "clientSecret": "s", "redirectUris": [REDIRECT]})

    asyncio.run(
        begin_login("acme", redirect_uri=REDIRECT, url="https://mcp.test/mcp?other", creds_path=creds, config_path=cfg)
    )

    assert len(fake.registrations) == 1
    assert _record(creds)["client_info"]["client_id"] == "client-1"


def test_a_static_client_pinned_to_another_issuer_is_refused(fake, creds):
    client = _login.StaticClient(client_id="c", client_secret="s", issuer="https://other-as.test")

    with pytest.raises(_bridge.LoginWontHelp, match="belongs to issuer"):
        _begin(creds, client=client)
    assert fake.token_requests == []


def test_a_static_client_pinned_to_the_right_issuer_proceeds(fake, creds):
    _begin(creds, client=_login.StaticClient(client_id="c", issuer=AS_URL + "/"))


def test_the_registered_redirect_spelling_is_sent_verbatim(fake, creds):
    registered = "https://Staffing.Example"
    client = _login.StaticClient(client_id="c", redirect_uris=(registered,))

    pending = _begin(creds, client=client, redirect_uri="https://staffing.example/")
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

    assert parse_qs(urlparse(pending.authorization_url).query)["redirect_uri"] == [registered]
    assert pending.redirect_uri == registered
    assert fake.token_requests[-1]["redirect_uri"] == registered


def test_an_unset_secret_variable_is_named(fake, creds, tmp_path, monkeypatch):
    monkeypatch.delenv("NOPE_SECRET", raising=False)
    cfg = _cfg(tmp_path, {"clientId": "cfg-app", "clientSecret": "${NOPE_SECRET}"})

    with pytest.raises(ValueError, match=r"\$\{NOPE_SECRET\}"):
        asyncio.run(begin_login("acme", redirect_uri=REDIRECT, creds_path=creds, config_path=cfg))
    assert fake.seen == []


# ── Parity with login() and an end-to-end call ───────────────────────────────


def test_the_entry_matches_what_logins_sdk_writes_would_store(fake, creds, tmp_path):
    """login() writes through FileTokenStorage.set_client_info / set_tokens (the SDK's seam)
    and `_persist_token_endpoint`; replaying the same objects through them gives this entry."""
    pending = _begin(creds)
    client_info = _record(creds)["client_info"]
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))
    ours = _store(creds)["acme"]

    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

    reference = _bridge.FileTokenStorage("acme", tmp_path / "ref.json")
    asyncio.run(reference.set_client_info(OAuthClientInformationFull.model_validate(client_info)))
    token_fields = {k: v for k, v in ours["tokens"].items() if k != "expires_at"}
    asyncio.run(reference.set_tokens(OAuthToken(**token_fields)))
    reference._mutate(lambda d: d["acme"].__setitem__("token_endpoint", f"{AS_URL}/token"))
    theirs = _store(tmp_path / "ref.json")["acme"]

    assert abs(ours["tokens"].pop("expires_at") - theirs["tokens"].pop("expires_at")) <= 5
    assert ours == theirs


def test_a_later_tool_call_works_over_the_saved_credential(fake, creds, monkeypatch):
    import httpx

    pending = _begin(creds)
    _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))
    seen_auth = []

    def mcp(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("authorization"))
        if request.headers.get("authorization") != "Bearer at-client-1":
            return httpx.Response(401)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            result = {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif msg["method"] == "tools/list":
            result = {"tools": [{"name": "whoami", "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": '{"user": "alice"}'}], "isError": False}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": result})

    real_factory = _bridge.create_mcp_http_client

    def factory(headers=None, auth=None, **kwargs):
        client = real_factory(headers=headers, auth=auth, **kwargs)
        client._transport = httpx.MockTransport(mcp)
        return client

    monkeypatch.setattr(_bridge, "create_mcp_http_client", factory)

    async def call():
        async with _bridge._http_session("acme", MCP_URL, creds_path=creds, cred_backend="file") as s:
            return await s.call_tool("whoami", {})

    result = asyncio.run(asyncio.wait_for(call(), 10))

    assert result.content[0].text == '{"user": "alice"}'
    assert seen_auth and set(seen_auth) == {"Bearer at-client-1"}
