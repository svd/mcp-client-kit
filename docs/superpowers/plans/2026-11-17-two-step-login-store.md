# Two-step login in the credential store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a pending two-step login in the credential store, so `complete_login()` needs only the server name and the callback URL, each pending login is single-use, a new one supersedes the old, and `cancel_login()` drops one.

**Architecture:** `mcpgen/_login.py` gains a private `_PendingRecord` (today's `PendingLogin` fields) written under the server entry's `pending_login` key by `begin_login()` and taken back under `_store_locks` by `complete_login()`. The public `PendingLogin` shrinks to non-secret fields. `list_creds`, `migrate-creds` and registration reuse learn the new key.

**Tech Stack:** Python 3.11+, `mcp<2` SDK, httpx, pytest (sync tests calling `asyncio.run`).

**Spec:** `docs/superpowers/specs/2026-11-17-two-step-login-store-design.md`

## Global Constraints

- Entry key for the record: `pending_login`; record `format` value: `1`.
- Every store read-modify-write goes through `_store_locks(storage._lock_backend, storage._path)` and `_load_store_for_login(storage)` / `storage._save(data)`.
- No exception message, log line or `repr` may contain the callback URL, the code, `code_verifier`, a token or a `client_secret`.
- `login()` behaviour unchanged.
- Run before each commit: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` — all clean.
- Comments describe the code as it stands (CLAUDE.md "Comments").
- CHANGELOG `## Unreleased` entry and `doc/USAGE.md` two-step section rewritten to the new API in the task that changes it; state the design, contrast only with what the repo had.
- One commit per task.

## Review Focus

1. A token refresh (`FileTokenStorage.set_tokens`) between begin and complete must keep `pending_login` — test in Task 1.
2. A `begin_login()` that lands while `complete_login()` is exchanging must survive the final write — test in Task 1.
3. A replayed callback after success, and two concurrent completes with the same callback, must yield exactly one exchange — test in Task 1.
4. A corrupt store at begin is quarantined, not crashed on or overwritten silently — test in Task 1.
5. `delete-creds`/`delete_cred` on a server with only a pending login removes it and clears an otherwise empty store — test in Task 3.

## Review rulings folded in (spec review 2026-11-17)

Record validated in full when read (unusable → no pending login, errors swallowed); keyring fallback during the take raises `LoginNotCompleted`; `AuthorizationDenied` also subclasses `ValueError` (Task 1). `list_creds()` lists only entries with `tokens`; `migrate-creds` strips `pending_login`; registration reuse also reads `pending_login.client_info` (Task 2).

---

### Task 1: Pending login stored; `complete_login(server_name, callback_url)`

**Files:**
- Modify: `mcpgen/_login.py` (module docstring, `PendingLogin`, `begin_login`, `_read_callback`, `_restore_provider`, `complete_login`, `_stored_entry` docstring)
- Modify: `mcpgen/__init__.py` (exports unchanged unless a name is removed)
- Modify: `tests/test_login_split.py`
- Modify: `CHANGELOG.md`, `doc/USAGE.md`

**Interfaces:**
- Produces:
  - `PendingLogin(server_name: str, redirect_uri: str, authorization_url: str, state: str, expires_at: float)`, frozen, property `expired`. No `to_json`/`from_json`.
  - `_PendingRecord` (frozen dataclass, `repr=False`): `server_url, redirect_uri, authorization_url, state, expires_at, code_verifier, client_info, client_metadata, oauth_metadata, protected_resource_metadata, auth_server_url, protocol_version`; `to_dict() -> dict` (adds `"format": 1`), `from_dict(raw: object) -> _PendingRecord | None` (None for a wrong format, missing field, non-dict).
  - `_PENDING_KEY = "pending_login"`.
  - `_take_pending(storage: FileTokenStorage, server_name: str, callback_url: str) -> _PendingRecord` — locked; raises per spec step 1; pops and saves on match.
  - `async complete_login(server_name: str, callback_url: str, *, creds_path: Path | str = DEFAULT_CREDS_PATH, cred_backend: str | None = None, verify: bool = True) -> LoginResult`.

- [ ] **Step 1: Rewrite test helpers and existing tests to the new API**

`_complete(pending, callback, **kw)` → `complete_login(pending.server_name, callback, creds_path=<creds>, cred_backend="file", verify=False, **kw)` (helper takes `creds`). Tests reading `pending.client_info` / `pending.code_verifier` read `_store(creds)["acme"]["pending_login"]` instead. Replace `test_complete_after_json_round_trip_writes_the_entry_login_writes` with `test_complete_writes_the_entry_login_writes_and_removes_the_record` (asserts `set(entry) == {"client_info", "tokens", "token_endpoint"}`). Replace `test_begin_returns_an_authorization_url_and_leaves_the_store_untouched` with `test_begin_stores_the_record_and_keeps_the_tokens`:

```python
pending = _begin(creds)
entry = _store(creds)["acme"]
assert entry["tokens"] == PRIOR["tokens"] and entry["client_info"] == PRIOR["client_info"]
record = entry["pending_login"]
assert record["format"] == 1 and record["state"] == pending.state
assert record["redirect_uri"] == REDIRECT and record["code_verifier"]
assert oct(creds.stat().st_mode & 0o777) == "0o600"
```

The two-process test passes only server name + callback URL to the second process (the first prints `authorization_url`). The repr test asserts `repr(pending)` holds neither the stored verifier nor a static client's secret, and `PendingLogin` has no attribute `code_verifier`.

- [ ] **Step 2: Add new failing tests**

```python
def test_second_begin_supersedes_the_first(fake, creds):
    first = _begin(creds); second = _begin(creds)
    with pytest.raises(LoginStateMismatch):
        _complete(creds, first.server_name, FakeOAuth.authorize(first.authorization_url))
    assert _store(creds)["acme"]["pending_login"]["state"] == second.state
    _complete(creds, "acme", FakeOAuth.authorize(second.authorization_url))

def test_replayed_callback_after_success_is_refused(fake, creds): ...  # second call → LoginStateMismatch, one token request in fake
def test_two_concurrent_completes_make_one_exchange(fake, creds): ...  # asyncio.gather; one result, one LoginStateMismatch; fake token-request count == 1
def test_complete_without_a_pending_login_is_a_state_mismatch(fake, creds): ...
def test_mismatched_state_keeps_the_record(fake, creds): ...
def test_error_without_state_keeps_the_record(fake, creds): ...     # AuthorizationDenied; record still there
@pytest.mark.parametrize("case", ["expired", "denied", "no_code", "iss", "exchange_rejected"])
def test_failure_after_the_state_matched_removes_the_record(fake, creds, case): ...  # record gone; PRIOR tokens kept
def test_refresh_between_begin_and_complete_keeps_the_record(fake, creds): ...  # FileTokenStorage.set_tokens in between
def test_begin_during_the_exchange_survives_the_final_write(fake, creds, monkeypatch): ...  # patch exchange to call begin_login mid-way; newer pending_login kept
def test_begin_quarantines_a_corrupt_store(fake, tmp_path): ...  # same outcome login() gives for corrupt JSON
def test_begin_on_a_server_with_no_entry_creates_a_pending_only_entry(fake, tmp_path): ...
def test_unknown_record_format_is_no_pending_login(fake, creds): ...
```

`FakeOAuth` (`tests/_fake_oauth.py`) gains a counter of token-endpoint authorization-code requests if it has none.

def test_unusable_record_is_no_pending_login(fake, creds): ...  # record with client_info {"client_secret": "shh", "redirect_uris": "bad"} → LoginStateMismatch; "shh" not in str(exc) nor exc.__context__ text
def test_keyring_fallback_during_the_take_refuses_the_exchange(fake, creds, monkeypatch): ...  # flip storage._backend in a patched _save → LoginNotCompleted; fake.token_requests == []
def test_authorization_denied_is_a_value_error(): assert issubclass(AuthorizationDenied, ValueError)

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_login_split.py -q`
Expected: the new and rewritten tests fail (TypeError on `complete_login` signature / KeyError `pending_login`).

- [ ] **Step 4: Implement in `mcpgen/_login.py`**

- `begin_login`: after the round, build `_PendingRecord`; under `_store_locks`, `data = _load_store_for_login(storage)`, `data.setdefault(server_name, {})[_PENDING_KEY] = record.to_dict()`, `storage._save(data)`; return the public `PendingLogin`. `_stored_entry` stays the pre-round read for registration reuse; its docstring drops "changes nothing on disk".
- `_take_pending`: implements spec step 1 order — no record → mismatch; callback `state` present and ≠ record (constant-time compare) → mismatch, no write; no `state` → `AuthorizationDenied` if `error` else mismatch, no write; match → pop key (drop the entry if it becomes empty), save, return record.
- `_read_callback(record, callback_url)` keeps the post-match checks (expired first, then `iss`, `error`, `code`).
- `_restore_provider(record, staging)` takes the record.
- `complete_login`: storage from `creds_path` + `resolve_cred_backend(cred_backend)`; `_take_pending`; checks; exchange; final locked write `new = {client_info, tokens, token_endpoint}`, carrying over `data.get(server_name, {}).get(_PENDING_KEY)` when present; verify as today using `record.server_url`.
- `_PendingRecord.from_dict` validates `client_info`, `client_metadata`, `oauth_metadata`, `protected_resource_metadata` with the SDK models inside `try/except Exception` → None.
- `_take_pending`: after `storage._save`, if `storage._backend` differs from the backend read, raise `LoginNotCompleted` (record would survive in the keyring).
- `AuthorizationDenied(LoginNotCompleted, ValueError)`.

- [ ] **Step 5: Run the full suite and linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Docs**

`doc/USAGE.md` two-step section: example with `begin_login` → redirect → `complete_login(server_name, str(request.url))`; routing several servers through one callback via `callback_state` + the `PendingLogin.state` the caller kept; the spec's error table; where secrets live (the store). CHANGELOG Unreleased two-step bullet rewritten to this API.

- [ ] **Step 7: Commit**

```bash
git add mcpgen tests CHANGELOG.md doc/USAGE.md
git commit -m "feat!: keep the pending two-step login in the credential store"
```

---

### Task 2: Store neighbours — `list_creds`, `migrate-creds`, registration reuse

**Files:**
- Modify: `mcpgen/_bridge.py` (`list_creds`, the migrate function whose merge is `{**target, **source_subset}`), `mcpgen/_login.py` (`_reusable_registration`, `begin_login` reuse block)
- Test: `tests/test_bridge.py`, `tests/test_login_split.py`
- Docs: CHANGELOG bullet for the `list-creds` change.

**Interfaces:**
- Consumes: `_PENDING_KEY`, `_PendingRecord.from_dict` (Task 1).

- [ ] **Step 1: Failing tests**
  - `test_list_creds_lists_only_entries_with_tokens` — `{"a": {"pending_login": {...}}, "b": {"client_info": {...}}, "c": {"tokens": {...}}}` → names `["c"]`; an entry with tokens and `pending_login` is listed. Adjust any existing list_creds test that relies on a token-less entry being listed.
  - `test_migrate_strips_pending_logins` — source `{"a": {"pending_login": {...}}, "b": {"tokens": {...}, "pending_login": {...}}}`, target `{"a": {"tokens": T}}` → target `a` still `{"tokens": T}`, target `b` has no `pending_login`; result `migrated == 1`, `overwritten == 0`.
  - `test_second_begin_reuses_the_client_the_first_registered` — begin twice on a store with no top-level registration for `REDIRECT` → `len(fake.registrations) == 1`, both records carry `client-1`.
  - `test_a_pending_registration_from_a_moved_authorization_server_is_not_reused` — mirror of the existing moved-AS test, with the client only under `pending_login`.

- [ ] **Step 2: Run, expect failures.** `uv run pytest tests/test_bridge.py tests/test_login_split.py -k "list_creds or migrate or reuses or moved" -q`

- [ ] **Step 3: Implement**
  - `list_creds`: skip an entry without `tokens`.
  - migrate: build the copied subset as entries minus `pending_login`, dropping empty ones; `overwritten`/`migrated` count that subset; purge removes the keys requested as today.
  - reuse: candidates in order — top-level `client_info` with `entry["token_endpoint"]`, then `pending_login.client_info` with the record's `oauth_metadata["token_endpoint"]`; the existing moved-AS re-registration compares against the candidate's endpoint.

- [ ] **Step 4: Full suite + linters.** Expected: pass.

- [ ] **Step 5: Commit** `feat: list-creds, migrate-creds and registration reuse know pending logins`

---

### Task 3: `cancel_login()`

**Files:**
- Modify: `mcpgen/_login.py`, `mcpgen/__init__.py` (export), `tests/test_login_split.py`, `doc/USAGE.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: `_PENDING_KEY`, `_store_locks`, `_load_store_for_login` (Task 1).
- Produces: `async cancel_login(server_name: str, *, creds_path: Path | str = DEFAULT_CREDS_PATH, cred_backend: str | None = None) -> bool`.

- [ ] **Step 1: Failing tests**

```python
def test_cancel_removes_only_the_record(fake, creds):
    pending = _begin(creds)
    assert asyncio.run(cancel_login("acme", creds_path=creds, cred_backend="file")) is True
    entry = _store(creds)["acme"]
    assert "pending_login" not in entry and entry["tokens"] == PRIOR["tokens"]
    with pytest.raises(LoginStateMismatch):
        _complete(creds, "acme", FakeOAuth.authorize(pending.authorization_url))

def test_cancel_without_a_pending_login_returns_false(fake, creds): ...
def test_cancel_drops_a_pending_only_entry_and_clears_an_empty_store(fake, tmp_path): ...  # file unlinked
def test_delete_cred_removes_a_pending_only_entry(fake, tmp_path): ...  # _bridge.delete_cred → True, store cleared
```

- [ ] **Step 2: Run, expect ImportError/fail.** `uv run pytest tests/test_login_split.py -k cancel -q`

- [ ] **Step 3: Implement** — locked load; pop key; drop empty entry; `storage._save(data)` when entries remain, else clear the backend the way `delete_cred` does (`_clear_backend(backend, path)`).

- [ ] **Step 4: Full suite + linters** (Global Constraints command). Expected: pass.

- [ ] **Step 5: Docs** — USAGE: one paragraph + signature; CHANGELOG bullet.

- [ ] **Step 6: Commit** `feat: cancel_login() drops a pending two-step login`

---

### Task 4: `redirect_uri` must be `https` or loopback `http`

**Files:**
- Modify: `mcpgen/_login.py` (`_validate_redirect_uri`, `StaticClient._redirect_spelling` or `begin_login` call site), `tests/test_login_split.py`, `doc/USAGE.md`, `CHANGELOG.md`

**Interfaces:**
- Produces: `_validate_redirect_uri(redirect_uri: str) -> None` raising `ValueError` mentioning `redirect_uri`.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize("uri", [
    "http://staffing.example/cb", "http://10.0.0.5:8080/cb", "ftp://h.example/cb", "com.example.app:/cb",
])
def test_begin_refuses_a_redirect_uri_without_tls(fake, creds, uri): ...  # ValueError match "redirect_uri"; no request in fake

@pytest.mark.parametrize("uri", [
    "http://localhost:8080/cb", "http://127.0.0.1/cb", "http://127.8.9.10:1/cb", "http://[::1]:9/cb", "https://h.example/cb",
])
def test_begin_accepts_https_and_loopback_http(fake, creds, uri): ...

def test_a_static_client_registered_uri_is_held_to_the_same_rule(fake, creds): ...  # StaticClient(redirect_uris=("http://intranet.example/cb",)) → ValueError
```

- [ ] **Step 2: Run, expect failures.** `uv run pytest tests/test_login_split.py -k "tls or loopback or same_rule" -q`

- [ ] **Step 3: Implement** — scheme `https`, or `http` with hostname `localhost` or an `ipaddress.ip_address` whose `.is_loopback`; keep the absolute/fragment checks. The rule is checked on the caller's `redirect_uri`, which a static client then matches against its registered URIs, so one check covers both.

- [ ] **Step 4: Full suite + linters.** Expected: pass (existing tests use `https://` or are updated).

- [ ] **Step 5: Docs** — USAGE sentence; CHANGELOG bullet.

- [ ] **Step 6: Commit** `feat: begin_login() requires an https or loopback redirect URI`

---

### Task 5: Refresh of a credential saved for an `https` redirect URI

**Files:**
- Modify: `tests/test_login_split.py` (test only)

- [ ] **Step 1: Test** `test_a_two_step_credential_refreshes_and_serves_a_tool_call`: begin + complete with `REDIRECT`; set `tokens.expires_at` to the past in the store; drive `_bridge._pre_flight_refresh("acme", storage)` against the fake token endpoint (`grant_type=refresh_token`; extend `FakeOAuth` if it has no refresh grant); assert new access token stored, `client_info.redirect_uris == [REDIRECT]`, `pending_login` absent; then a tool call through `_http_session` as in the existing end-to-end test succeeds.

- [ ] **Step 2: Run** `uv run pytest tests/test_login_split.py -k refreshes -q`. Expected: PASS (behaviour exists). If it fails, stop and debug with superpowers:systematic-debugging — the spec says no change is needed.

- [ ] **Step 3: Full suite + linters; commit** `test: a two-step credential for an https redirect URI refreshes`
