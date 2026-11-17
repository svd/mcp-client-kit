# Two-step login: pending logins kept in the credential store

Date: 2026-11-17. Status: approved for implementation.

## Problem

`begin_login()` / `complete_login()` (`mcpgen/_login.py`) split an OAuth login into two calls
so a web server can own the redirect URI. Between the calls the caller holds the whole
`PendingLogin`: PKCE `code_verifier`, the client registration (with `client_secret` for a
pre-registered confidential client) and the discovered metadata, serialized with
`to_json()` into a server-side session or an encrypted cookie.

A caller-held blob has three gaps for a server-side caller:

1. The caller must store secrets safely, and `PendingLogin.from_json()` will complete a
   login from any copy of the blob, however old or however many times it is replayed,
   until `expires_at`.
2. Nothing makes a pending login single-use. Two callbacks carrying the same `state`
   both reach the token endpoint; the second fails there instead of in mcpgen.
3. A second `begin_login()` for the same server leaves the first one valid, and there is
   no way to cancel one.

## Design

The pending login lives in the credential store. The caller holds only public values.

### Storage

After its network round, `begin_login()` writes the pending record to the server's entry
in the store under the key `pending_login`. The write re-reads the store inside
`_store_locks` (through `_load_store_for_login()`, so a corrupt store is quarantined as
`login()` and `complete_login()` do) and sets only that key, with the store's backend and
file mode. The entry's `tokens`, `client_info` and `token_endpoint` are not touched, so the
current credential, including a refresh that landed during discovery, keeps working until
`complete_login()` replaces it. A server with no entry gets one holding only
`pending_login`.

The record holds what the code exchange needs: `server_url`, `redirect_uri` (the spelling
sent on the wire), `authorization_url`, `state`, `expires_at`, `code_verifier`,
`client_info`, `client_metadata`, `oauth_metadata`, `protected_resource_metadata`,
`auth_server_url`, `protocol_version`, plus `format` (currently 1). A record is usable when
it has this `format`, every field, and its `client_info`, `client_metadata` and metadata
validate as the SDK models they came from. An unusable record counts as no pending login.
Validation errors are swallowed whole: their text quotes input values, which may include a
`client_secret`.

One pending login per server: `begin_login()` overwrites any existing `pending_login`, so
the earlier `state` stops being accepted. An expired record stays in the store until the
next `begin_login()`, `complete_login()` or `cancel_login()` for that server, or
`delete-creds`.

Registration reuse: a public client registered for the same redirect URI by an earlier
`begin_login()` that never completed is reused from `pending_login.client_info`, under the
same rules as a stored `client_info` (public client, redirect URI listed, secret not
expired, authorization server unmoved, compared against the token endpoint in the record's
`oauth_metadata`). The top-level `client_info` is tried first.

Other writers and readers:

- Token refresh, `set_client_info` and `_persist_token_endpoint` set single keys of the
  entry and keep the record.
- `login()` takes the whole entry out of the store before its round, and puts it back only
  when it got no token. A `login()` that produced a token drops the pending login,
  `PostLoginCheckFailed` included. A two-step login racing a `login()` for the same server
  loses its record.
- `delete-creds` / `delete_cred` remove the whole entry, pending login included.
- `migrate-creds` does not carry pending logins: it strips `pending_login` from the entries
  it copies and skips an entry left empty, so a pending login never overwrites a
  credential in the target. The counts cover the copied entries only.
- `list_creds()` lists only entries that hold `tokens`.

### `PendingLogin`

The public return value of `begin_login()`:

```python
@dataclass(frozen=True)
class PendingLogin:
    server_name: str
    redirect_uri: str
    authorization_url: str
    state: str
    expires_at: float  # epoch seconds

    @property
    def expired(self) -> bool: ...
```

It carries no secret; `to_json()` / `from_json()` are removed. `callback_state(url)` stays,
for a caller that routes one callback URL for several servers by `state`.

### `begin_login()`

Signature unchanged (`redirect_uri`, `creds_path`, `url`, `client_name`, `cred_backend`,
`config_path`, `ttl`, `reuse_registration`, `client`). Discovery, registration reuse and
static clients behave as before. New:

- `redirect_uri` must use `https`, or `http` with a loopback host (`localhost`,
  `127.0.0.0/8`, `::1`): RFC 6749 §3.1.2.1 asks a redirection endpoint to use TLS, and
  RFC 8252 §7.3 exempts the loopback interface. Any other scheme raises ValueError before
  any request. A static client's registered URIs are held to the same rule.
- On success the record is persisted (above) and the public `PendingLogin` returned.
  Nothing is written when discovery or registration fails.

### `complete_login()`

```python
async def complete_login(
    server_name: str,
    callback_url: str,
    *,
    creds_path: Path | str = DEFAULT_CREDS_PATH,
    cred_backend: str | None = None,
    verify: bool = True,
) -> LoginResult
```

Steps:

1. Under `_store_locks`: load the store and the server's `pending_login`.
   - A store that does not parse: `LoginNotCompleted`; the store is left where it is.
     Nothing is moved aside, because this step writes no fresh entry in its place.
   - No usable record: `LoginStateMismatch` (no pending login for the server).
   - The callback carries a `state` that differs from the record's: `LoginStateMismatch`;
     the record stays, so a stray or forged callback cannot cancel the real login.
   - The callback carries no `state`: `AuthorizationDenied` when it carries `error=`,
     otherwise `LoginStateMismatch`; the record stays.
   - The `state` matches: the record is removed from the store in the same locked write.
     From here on the login is used up, whatever happens next.
2. Checks on the taken record, each raising with the previous credential in place:
   expired → `PendingLoginExpired`; `iss` naming another issuer (RFC 9207) →
   `LoginStateMismatch`; `error=` → `AuthorizationDenied`; no `code` →
   `LoginNotCompleted`.
3. Exchange the code with the stored `code_verifier`; a refusal or a 200 without a usable
   token → `CodeExchangeRejected`.
4. Under `_store_locks`: write `client_info`, `tokens` and `token_endpoint` as the server's
   entry. A `pending_login` present at this point was written by a `begin_login()` that ran
   during the exchange; it is kept. A keyring that refuses this write raises
   `LoginNotCompleted`, with the previous credential in place.
5. With `verify`, `list_tools()` over the new credential; a failure raises
   `PostLoginCheckFailed` with the token saved.

Taking the record in step 1, before the exchange, makes two concurrent callbacks with the
same `state` resolve to one exchange; the other raises `LoginStateMismatch`. That holds
while the store lock is held: `_store_lock` proceeds unlocked where no lock can be taken
(NFS `ENOLCK`, the Windows timeout, no lock primitive), and there two callbacks can both
take the record.

Every store write of the two-step calls skips `FileTokenStorage`'s fallback to the file: a
keyring that refuses the write raises, and nothing is written anywhere. A fallback would put
the whole store, every server's tokens, in the plaintext file while the keyring kept the old
version, which the next keyring read would see. So `begin_login()` raises `RuntimeError`
with no record stored, step 1 raises `LoginNotCompleted` before the exchange with the record
still in the keyring, and step 4 raises `LoginNotCompleted`.

No message quotes the callback URL, the code, the verifier or a secret.

### `cancel_login()`

```python
async def cancel_login(
    server_name: str,
    *,
    creds_path: Path | str = DEFAULT_CREDS_PATH,
    cred_backend: str | None = None,
) -> bool
```

Removes the server's `pending_login` under `_store_locks`; returns whether there was one.
An entry left empty is removed; the store is cleared when no entry remains, as
`delete_cred()` does. Tokens are untouched. A store that does not parse, or a keyring that
refuses the write, raises `RuntimeError` and leaves the store as it was.

### Refresh

`_pre_flight_refresh` renews against the stored `token_endpoint` with the stored client,
and `_http_session` builds its SDK client metadata from `client_info.redirect_uris[0]`.
Neither depends on the redirect URI being loopback; a test pins that a credential saved by
`complete_login()` for an `https` redirect URI refreshes and is used for a tool call.

## Errors

| Raised by `complete_login` | When | Pending login | Previous credential |
|---|---|---|---|
| `LoginStateMismatch` | no pending login, a different or missing `state` | kept | kept |
| `AuthorizationDenied` | `error=` without `state` | kept | kept |
| `PendingLoginExpired` | past `expires_at` | removed | kept |
| `LoginStateMismatch` | `iss` names another issuer | removed | kept |
| `AuthorizationDenied` | `error=` with the matching `state` | removed | kept |
| `LoginNotCompleted` | neither `code` nor `error` | removed | kept |
| `CodeExchangeRejected` | the token endpoint refused the code | removed | kept |
| `PostLoginCheckFailed` | `verify` and `list_tools()` failed | removed | replaced |
| `LoginNotCompleted` | the store does not parse, or the keyring refused step 1's write | kept | kept |
| `LoginNotCompleted` | the keyring refused step 4's write | removed | kept |

`AuthorizationDenied` subclasses `ValueError` as well as `LoginNotCompleted`, so a caller
catching the `ValueError` that `login()` raises for a denied authorization catches it too.

## Surfaces

`mcpgen/__init__.py` exports `cancel_login`. `CHANGELOG.md` (Unreleased) and the two-step
section of `doc/USAGE.md` describe this API; their `to_json()` / `from_json()` text goes.

## Out of scope

- `login()` keeps its own round and local listener.
- No CLI surface for the two-step calls.
- More than one pending login per server.

## Testing

`tests/test_login_split.py` with `tests/_fake_oauth.py`:

- begin persists a record with the store's mode and leaves `tokens` alone; repr and the
  returned object carry no verifier or secret.
- complete from a fresh process view (only server name and callback URL) writes the entry
  `login()` writes and removes the record.
- a second begin supersedes the first state; a replayed callback after success raises
  `LoginStateMismatch`; two concurrent completes produce one exchange.
- each row of the error table, including which outcomes keep the record.
- a token refresh between begin and complete keeps the record.
- an unusable record (wrong format, missing field, invalid `client_info` carrying a secret)
  is no pending login, and the secret appears in no exception text.
- a keyring refusal in `begin_login()`, step 1 or step 4 raises and writes no file.
- a corrupt store in step 1 or `cancel_login()` raises and stays where it is.
- `cancel_login` removes only the record, and the whole entry when it held nothing else.
- `list_creds()` skips an entry without `tokens`; `migrate-creds` strips the record and
  never lets a pending-only entry overwrite a credential.
- a second `begin_login()` reuses the client the first one registered.
- `redirect_uri` scheme rules, including static-client URIs.
- the refresh test above.
