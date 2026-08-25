# mcp-client-kit usage

> **Availability:** this doc describes the published flow (PyPI + marketplace). Not yet
> live — for now use [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md).

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Claude Code users typically have it; otherwise
  `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Path A — Plugin / skill (recommended)

The plugin bundles the `generate-mcp-wrappers` skill, which drives the engine for you.

The skill requires the `mcpgen` CLI on `PATH` and checks for it before running. Install
it separately (`uv tool install mcp-client-kit`, or `uv add mcp-client-kit` in a project);
the plugin does not bundle it. You can also invoke the CLI ad hoc without installing via
`uvx --from mcp-client-kit mcpgen …`, but the skill itself needs `mcpgen` resolvable on
`PATH`.

**Install the plugin:**

```
/plugin marketplace add svd/mcp-client-kit
```

Or via the `svd-agent-skills` aggregator if it's listed there.

**Invoke the skill** in any Claude Code session:

```
/mcp-client-kit:generate-mcp-wrappers
```

The skill:
1. Generates mechanical stubs (`mcpgen codegen`) for the target server — typed inputs,
   `Any` returns.
2. Probes chosen tools live (`mcpgen probe`) to capture actual response shapes.
3. Edits the shape-spec — the judgment pass over what the probes observed.
4. Regenerates wrappers, now with real return types.

Return types are never inferred from a tool's `inputSchema`; they come from steps 2–3.

Before the skill can reach your server, complete [§ Configure a server](#configure-a-server)
and [§ Authenticate](#authenticate).

For the full 7-step procedure see [§ The skill procedure](#the-skill-procedure).

---

## Path B — CLI only

Use `mcpgen` directly to generate wrappers or probe tools without the skill layer.

> The PyPI package is **`mcp-client-kit`**; the command it installs is **`mcpgen`**.

### One-off (no install)

```bash
uvx --from mcp-client-kit mcpgen codegen <server> --out <server>.py
uvx --from mcp-client-kit mcpgen probe <server> <tool> --args '{}' --emit-shape <server>.shapes.json
uvx --from mcp-client-kit mcpgen login <server>
```

### Persistent (on PATH)

```bash
uv tool install mcp-client-kit   # installs the mcpgen command on PATH
mcpgen codegen <server> --out <server>.py
```

### Project dependency

```bash
uv add mcp-client-kit            # or: pip install mcp-client-kit
```

### Command reference

| Command | Purpose | Key flags |
|---------|---------|-----------|
| `codegen <server>` | Emit wrappers (typed inputs; typed returns once `--shapes` is supplied) and a tool-inventory manifest | `--out`, `--shapes <path>`, `--probe <tool>` / `--probe-args`, `--embed-schema`, `--manifest <path>`, `--no-manifest` |
| `check <server>` | Compare the live tool inventory against a stored manifest | `--manifest <path>`, `--json`, `--update`, `--stdio` |
| `list <server>` | Tools as JSON `[{name, description}]` (add `--schema` for `inputSchema` per tool); discriminator advisory on stderr | `--schema` |
| `probe <server> <tool>` | Live call(s) → shape skeleton | `--args` (repeatable), `--emit-shape <path>` (writes `.parts/`) |
| `call <server> <tool>` | One live call, raw payload to disk — bootstrap ids / inspect output | `--out <path>` (required) |
| `merge <server>` | Consolidate `.parts/` → `<server>.shapes.json`; emit gitignored `verify.json` | `--out <path>` |
| `login <server>` | Browser OAuth login | `--headless` / `--no-headless`, `--callback-timeout <seconds>`, connection flags below |
| `migrate-creds` | Copy stored OAuth tokens between `file`/`keyring` backends | `--from`, `--to`, `--servers`, `--purge`, `--set-default`, `--creds <path>` |
| `discover` | List MCP servers configured in Claude Code | `--host <id>` (repeatable), `--json` |

Connection flags shared by `codegen`/`check`/`list`/`probe`/`call`/`login`: `--url`,
`--bearer`, `--config`, `--client-name`, `--cred-backend`, `--creds`, `--env`
(see [§ Authenticate](#authenticate)). `--stdio` is available on every command in that
list except `login`.

`discover` enumerates the MCP servers configured in **Claude Code**. Only Claude Code is
implemented today; `discovery.PROVIDERS` is the extension point where support for more
hosts gets added.

`--creds` is also accepted by the credential-management commands — `list-creds`,
`delete-creds`, `migrate-creds` — so a non-default store stays addressable from every
command that reads or writes it.

> **PII:** `call` writes the raw, unscrubbed payload. Name the file `*.probe-raw.json`
> (gitignored) and never commit it.

---

## Configure a server

Config resolves relative to your **current working directory**. Search order (first match wins):

1. `--config <path>` — explicit override
2. `$MCPGEN_SERVERS` env var — path to a JSON file
3. `~/.mcpgen/servers.json` — user-global fallback
4. `./.mcp.json` in cwd — Claude Code format

Copy the bundled template and edit:

```bash
cp <kit-root>/servers.example.json .mcp-servers.json
export MCPGEN_SERVERS=.mcp-servers.json
```

Both formats are accepted:

```jsonc
// Simple: name → URL
{ "myserver": "https://mcp.example.com/mcp/v1" }

// Claude Code format
{
  "mcpServers": {
    "myserver": {
      "type": "http",
      "url": "https://mcp.example.com/mcp/v1",
      "clientName": "my-app" 
    } 
  } 
}
```

### Supported transports

**stdio** and **Streamable HTTP** only.

SSE servers are surfaced by `mcpgen discover` but marked unsupported (`probeable: false`,
with an explanatory note). A config entry declaring `"type": "sse"` is refused up front
with an explicit error rather than failing obscurely part-way through a connection, so
`list` / `codegen` / `check` / `probe` / `call` all stop with a message naming SSE as the
cause.

Known limit: the refusal reads the declared `type` from your server config, so it only
fires for named config entries. A bare SSE URL passed as `<server>` or via `--url` carries
no declared type and will instead fail at the transport layer with the SDK's own error.
`--stdio` and `--url` assert a transport explicitly and skip the check by design; a
`--bearer` token does not, so a config-declared SSE entry is still refused with it.

---

## Authenticate

**OAuth (most servers):**

```bash
mcpgen login myserver   # opens browser; token stored at ~/.mcpgen/credentials.json
```

Re-run when you see `ReauthenticationRequired` — that is the one error a trip through the
browser fixes. mcpgen raises it when the credential is gone: either there is nothing
cached to refresh with (no refresh token, no client id, or credentials predating the
version that started caching the token endpoint), or the authorization server named it
dead — the refresh token (`invalid_grant`) or the client registration (`invalid_client`,
`unauthorized_client`, which a fresh login re-registers).

`TokenRefreshUnavailable` is everything else: the cached refresh token is untouched, so
the browser has nothing to replace. Its message says whether to retry or fix a
configuration.

The browser flow waits up to 300 seconds for the redirect. If you cancel on the consent
screen, many authorization servers just close the tab without redirecting back, so mcpgen
never hears anything — the wait then ends with `TimeoutError` instead of hanging. Your
existing credential is left untouched by an attempt that never got as far as a token.

Once the authorization server *has* issued a token, that token wins. If the login
completes and the check that follows it fails — a `502` from the origin, a `401` over
scope or audience, an MCP-level error on the first call — the new credential is kept
and `login` raises `PostLoginCheckFailed` instead of the raw error. Whatever the cause,
logging in again will not change it, so read the message before retrying.

Adjust the bound when the default doesn't fit — a hardware token or an approve-on-phone
step can outlast it, and a scripted login may want to fail sooner:

```bash
mcpgen login myserver --callback-timeout 900   # 15 minutes
mcpgen login myserver --callback-timeout 0     # wait forever (no bound)
```

Negative or non-numeric values are rejected outright. Headless logins ignore the flag —
the pasted-URL prompt is never bounded. In code, the same value is `callback_timeout=` on
`login()`, `ensure_login()`, and `ensure_login_all()`.

**No browser available (container, SSH, CI):**

```bash
mcpgen login myserver --headless
```

mcpgen prints the authorization URL instead of opening it, and waits:

```
Open this URL in your browser:

https://auth.example.com/authorize?response_type=code&client_id=…

After authorizing, paste the full callback URL here (http://localhost.../callback?code=...):
```

Authorize on any device, then copy the URL from the browser's address bar — the page
itself will fail to load, which is expected, nothing is listening on that address — and
paste it at the prompt.

Headless mode is auto-detected when neither `DISPLAY` nor `WAYLAND_DISPLAY` is set (macOS
and Windows are always treated as interactive). Set `MCPGEN_HEADLESS=1` or `0` to override
the detection; an explicit `--headless` / `--no-headless` overrides both.

**PAT / Bearer token:**

```bash
export MYSERVER_TOKEN="pat_..."
mcpgen codegen myserver --bearer "$MYSERVER_TOKEN" --out myserver.py
```

Never pass a literal token on the command line; always read from an env var.

**Local stdio server (no auth):**

```bash
mcpgen codegen myserver --stdio "python path/to/server.py" --out myserver.py
```

**Credential storage backend:**

OAuth tokens are stored via one of three backends, selected (highest priority first)
by `--cred-backend`, then `$MCPGEN_CRED_BACKEND`, then `~/.mcpgen/config.json`
(`{"cred_backend": "..."}`), defaulting to `file`.

| Backend | Storage |
|---------|---------|
| `file` | `~/.mcpgen/credentials.json` (chmod 0600) — works everywhere **(default)** |
| `keyring` | OS native keystore (Keychain / Credential Locker / Secret Service); falls back to `file` with a warning if the keystore is unavailable |
| `auto` | Try `keyring`; if keystore is unavailable fall back to `file` silently — no warning |

```bash
mcpgen login myserver --cred-backend keyring
```

**Credential file location:** `--creds PATH` moves the `file` backend off
`~/.mcpgen/credentials.json` — useful to keep one project's tokens out of the shared
store, or to run two identities side by side. Pass the same path to every command
that touches those tokens; a login written to one file is invisible to a call reading
another:

```bash
mcpgen login myserver --creds ./.mcp-creds.json
mcpgen call myserver whoami --creds ./.mcp-creds.json --out out.json
mcpgen list-creds --creds ./.mcp-creds.json
```

The flag has no effect with `--bearer` or `--stdio`, which store no credentials. In
code the same value is `creds_path=` on `login()`, `ensure_login()`,
`ensure_login_all()`, `session()`, and `McpBridgeCaller(...)`; `mcpgen.DEFAULT_CREDS_PATH`
is exported as the default.

**Validate keyring storage** — confirm the token landed in the OS keystore, not the
fallback file (service `mcpgen`, username `credentials`):

```bash
python3 -c "import keyring; print(keyring.get_password('mcpgen', 'credentials'))"
```

If this prints JSON, the keyring backend succeeded.
If it prints `None` (or errors), the fallback file was used instead — check for a
`[mcpgen] keyring unavailable` warning in the `mcpgen login` output.

> **macOS note:** `security find-generic-password -w` prints raw binary, not JSON —
> use the Python command above instead.

**Set keyring as the permanent default** — so every `mcpgen` invocation uses it
without `--cred-backend`:

```bash
# Option A: config file (persists across shells)
echo '{"cred_backend": "keyring"}' > ~/.mcpgen/config.json

# Option B: env var (add to your shell profile, e.g. ~/.zshrc)
export MCPGEN_CRED_BACKEND=keyring
```

Priority order (highest first): `--cred-backend` flag → `$MCPGEN_CRED_BACKEND` →
`~/.mcpgen/config.json` → default (`file`).

**Migrate credentials between backends** — use `migrate-creds` to move stored OAuth tokens
from one backend to the other. Both `--from` and `--to` are required and must differ.

```bash
# Move all tokens from file → keyring and set keyring as the new default
mcpgen migrate-creds --from file --to keyring --set-default

# Migrate only selected servers (comma-separated)
mcpgen migrate-creds --from file --to keyring --servers myserver,otherserver

# Move and remove from source after a verified copy
mcpgen migrate-creds --from keyring --to file --purge
```

Behaviour:
- Reads the source backend, writes into the target backend, then **re-reads the target to verify** every migrated key landed (raises `RuntimeError` if any are missing).
- On collision (server already exists in target), **source wins** — the target entry is overwritten.
- `--servers A,B,C` filters to those names; any name not found in the source raises an error immediately (before writing).
- `--purge` deletes only the migrated keys from the source after a successful verified write; non-migrated keys are untouched.
- `--set-default` writes `{"cred_backend": "<to>"}` into `~/.mcpgen/config.json`, so every subsequent `mcpgen` invocation uses the new backend without a flag or env var (equivalent to the manual config edit above, but in one step).
- Empty source (no tokens stored) is a no-op; exits cleanly with `migrated: 0`.

---

## The skill procedure

Seven steps from invocation to typed wrappers:

| Step | What it does |
|------|-------------|
| 1. Mechanical stubs | `mcpgen codegen <server> --out <server>.py` — all tools, returns `Any` |
| 2. Curate | Pick tools whose payloads you want typed (not all of them) |
| 3. Probe → skeleton | `mcpgen probe <server> <tool> --args '...' --emit-shape <server>.shapes.json` — writes a per-tool part under `<server>.shapes.json.parts/` (parallel-safe; many probes can run at once) |
| 4. Merge | `mcpgen merge <server> --out <server>.shapes.json` — consolidate the `.parts/` into the committed shape-spec, preserving hand-edits for un-probed tools. Also emits a gitignored `<server>.verify.json` sidecar holding pre-scrub `probed_args` for roundtrip verification. Re-run after partial re-probes. |
| 5. Edit shape-spec | Set `unwrap`, `return_model`, `fields`, `input_overrides` — the judgment pass. For tools that return different shapes per input value, use `discriminator` + `variants` instead of a flat `return_model` — see [§ Polymorphic tools](#polymorphic-tools-discriminated-shaping). |
| 6. Regenerate | `mcpgen codegen <server> --out <server>.py --shapes <server>.shapes.json` |
| 7. Verify | `ast.parse` the module; confirm return types |

Optional step 8: generate a runnable smoke-test — see [§ Smoke-test runner](#smoke-test-runner).

Shape-spec keys (per tool): `unwrap` (envelope key path), `return_model` (TypedDict
name, or `null` for `Any`), `fields` (top-level scalar fields), `return_container`
(e.g. `"list"` when the unwrapped value is a list), `input_overrides` (fix schema-lie
types), `discriminator` + `variants` (polymorphic tools), and `source` (`"live"` vs
`"fixture"`). JSON/TS type tokens (`any`, `null`, `integer`) are normalized to Python
(`Any`, `None`, `int`) at load.

Multi-probe: repeat `--args` for each sample input; shapes are deep-merged (keys unioned,
type conflicts widened). Use when fields are nullable or a tool has multiple response shapes.

**Security:** `probe` records live call arguments verbatim in the shape parts. Before
committing, scrub `probed_args` in `<server>.shapes.json` — replace real ids/names/PII with
placeholders like `"<example-id>"`. Real values survive deletion via git history. The
`verify.json` sidecar keeps the unscrubbed args but is gitignored — never commit it.

Once generated, see [§ Using the generated wrappers](#using-the-generated-wrappers) to call them.

---

## Using the generated wrappers

Generated functions are `async`, take `caller` as the first positional argument, and
require all tool arguments as **keyword arguments**.

```python
async def get_me(caller: McpCaller) -> GitHubUser: ...
async def list_issues(caller: McpCaller, *, owner: str, repo: str) -> list[IssueSummary]: ...
```

Construct a `McpBridgeCaller` — the concrete caller that handles auth and transport —
and pass it when calling any generated function.

**Bearer / PAT example** (e.g. GitHub MCP at `https://api.githubcopilot.com/mcp/`):

```python
import asyncio
import os
from mcpgen import McpBridgeCaller
import github  # generated: mcpgen codegen github --out github.py --bearer "$GITHUB_TOKEN"

async def main():
    # caller carries auth/transport; the wrapper module stays backend-agnostic.
    caller = McpBridgeCaller(
        url="https://api.githubcopilot.com/mcp/",
        bearer=os.environ["GITHUB_TOKEN"],  # GitHub PAT
    )
    me = await github.get_me(caller)
    issues = await github.list_issues(caller, owner="octocat", repo="hello-world")
    print(me, issues)

asyncio.run(main())
```

**OAuth example** (automated login + automated token refresh):

```python
import asyncio
from mcpgen import McpBridgeCaller, ensure_login
import myserver  # generated: mcpgen codegen myserver --out myserver.py

SERVER = "myserver"
URL = "https://mcp.example.com/mcp/v1"

async def main():
    # Refresh-or-login: silent when a valid/refreshable token is cached;
    # opens the browser once only when login is actually required.
    await ensure_login(SERVER, url=URL)

    caller = McpBridgeCaller(url=URL)   # OAuth; no bearer token
    user = await myserver.whoami(caller)
    print(user)

asyncio.run(main())
```

How it works:

- **Token refresh is automatic** — every `.call()` runs a pre-flight refresh, so a
  near-expired access token is renewed silently from the stored refresh token with
  no browser interaction. `ensure_login` runs that same refresh before your first
  call and opens the browser only as a last resort.
- **`ensure_login` is idempotent** — safe to call before every run. When already
  authenticated it returns immediately. When the refresh token itself is expired (or
  absent), it falls back to a full browser login — the in-code equivalent of
  `mcpgen login <server>`. Credentials are persisted at
  `~/.mcpgen/credentials.json`.
- **Several servers at once** — `await ensure_login_all(["a", "b"])` runs the same
  refresh-or-login for each name in turn. Sequential on purpose: parallel logins would
  open several browser tabs at once and race for stdin in headless mode. Both functions
  accept `headless=True`/`False` to force the paste-the-URL or browser flow explicitly.
- **Lower-level alternative** — skip `ensure_login` and catch
  `ReauthenticationRequired` from the first failing `.call()`, then call
  `login(SERVER, url=URL)` and retry. `ReauthenticationRequired`, `LoginWontHelp`,
  `PostLoginCheckFailed`, `TokenRefreshUnavailable` and `login` are all exported from
  `mcpgen`.
- **Batch callers** — catch `LoginWontHelp` separately from `ReauthenticationRequired`.
  It is the base class for every auth failure another browser round cannot fix, so one
  `except` covers them all: `PostLoginCheckFailed` (the token was issued and cached but
  the check after it failed) and `TokenRefreshUnavailable` (the cached grant was not
  renewed, and not because the credential died). Abort the batch there instead of
  re-prompting once per item. Catch a subclass only when the difference matters.

`McpBridgeCaller` kwargs mirror the CLI connection flags — `url=`, `bearer=` (PAT),
`cmd=` (stdio), `config_path=`, `client_name=`. One instance is reusable across
multiple calls and multiple servers (the `SERVER` constant is baked into each
generated module, not into the caller).

For typing your own caller (e.g. in tests), implement `McpCaller`:

```python
from mcpgen import McpCaller
from typing import Any

class FakeCaller:
    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        return {"login": "octocat"}
```

---

## Reusing one connection for a series of calls

By default each wrapper call opens and closes its own MCP session. For a run that
makes several calls, wrap them in a connection block:

```python
from mcpgen import McpBridgeCaller
import demo

caller = McpBridgeCaller(cmd="python server.py")

async with caller.connected():
    greeting = await demo.greet(caller, name="Grace")
    total = await demo.add(caller, a=1, b=2)
```

Inside the block, all calls to one server share a single initialized session: one
`initialize()`, one stdio subprocess, one OAuth pre-flight refresh. Outside the
block, `call()` is unchanged — a session per invocation — so existing code keeps
working with no edits.

Details:

- **Per-server.** A block holds one session per distinct server name, opened on
  first use and closed on exit.
- **Concurrency.** Calls inside a block may run concurrently; `asyncio.gather()`
  over wrappers works. Only session creation is serialized, so two concurrent
  first-calls cannot start two subprocesses.
- **Cleanup.** Sessions close when the block exits, including when the body raises.
- **Not re-entrant.** Nesting `connected()` on the same caller raises `RuntimeError`.
- **Generated wrappers are unaffected.** They depend only on the `McpCaller`
  protocol and neither know nor care whether a block is active.

---

## Smoke-test runner

A second skill, `generate-mcp-runner`, authors a standalone `<server>/run.py` that
imports the generated wrappers and exercises them — a quick way to confirm the
wrappers actually work end-to-end.

Invoke it after the wrapper skill (optional step 8) with a phrase like
`generate runner for <server>`. It consumes the wrapper module (`<server>.py`),
`<server>.shapes.json`, and the `verify.json` sidecar (for real pre-scrub args), then:

- calls each **read-only** tool once (one call per discriminator variant), in a
  sensible workflow order (identity → metadata → discovery → detail → search);
- **skips mutating tools** by default (opt in via an explicit instruction);
- picks args from `verify.json` → scrubbed `probed_args` → schema-minimal synthetic;
- selects a connection skeleton matching the transport + auth (stdio / http public /
  bearer / oauth);
- **never auto-runs** — it only emits and statically validates (`ast.parse` +
  `py_compile`); run it yourself when ready:
  ```bash
  uv run <server>/run.py
  ```

---

## Drift detection (`mcpgen check`)

A generated wrapper is a snapshot of a remote contract you do not control. `check` tells
you when that contract has moved.

### The manifest

Every `mcpgen codegen` run that writes a module also writes a **tool-inventory manifest**
beside it, derived from the `--out` stem:

```bash
mcpgen codegen demo --stdio 'python server.py' --out gen/demo.py
# writes gen/demo.py  and  gen/demo.mcpgen.json
```

| Flag | Effect |
|------|--------|
| *(none)* | Write `<out-stem>.mcpgen.json` next to `--out` |
| `--manifest PATH` | Write it to `PATH` instead |
| `--no-manifest` | Do not write one |

Stdout mode (no `--out`) writes no manifest.

The file holds exactly three keys — `format_version`, `server`, and `tools` (each tool's
`description`, `inputSchema`, and `annotations`). There is **no timestamp and no generator
version**, so the manifest is a pure function of the inventory: regenerating against an
unchanged server produces a byte-identical file and never dirties a git diff. Object keys
are sorted and set-like schema arrays (`required`, `enum`) are normalized, so a server that
merely re-orders them does not read as a change. It contains no credentials — commit it
alongside the generated module.

### Checking

```bash
mcpgen check <server> [--manifest PATH] [--json] [--update] [--stdio CMD] <connection flags>
```

| Flag | Meaning |
|------|---------|
| `--manifest PATH` | Manifest to compare against (default: `<server>.mcpgen.json`) |
| `--json` | Emit a structured report on stdout instead of human text |
| `--update` | Accept the live inventory: rewrite the manifest and exit 0 |
| `--stdio CMD` | stdio transport, e.g. `'python server.py'` |

Plus the same connection flags as `codegen`: `--url`, `--bearer`, `--client-name`,
`--config`, `--creds`, `--cred-backend`, `--env`.

### Exit codes

| Exit | Meaning |
|------|---------|
| `0` | No drift. Description-only changes print as **advisories** and do not affect the exit code. |
| `1` | Drift: a tool was added, removed, or its `inputSchema` or `annotations` changed. |
| `2` | Operational error: manifest missing (without `--update`) or unreadable, unknown `format_version`, transport failure, auth failure, bad config. |

`2` is never drift. A dead CI runner or an expired token can therefore never be misread as
a changed tool contract — the two failure modes stay distinguishable in a pipeline.

```yaml
- name: Check MCP tool drift
  run: mcpgen check demo --config .mcp.json --manifest gen/demo.mcpgen.json --json
```

### Accepting a change

`check` never rewrites the manifest on its own — not when it finds drift, not silently.
Ask for it:

```bash
mcpgen check demo --stdio 'python server.py' --manifest gen/demo.mcpgen.json --update
```

`--update` writes the live inventory and exits `0`. It also bootstraps a manifest for an
existing wrapper that predates one. On an operational error (exit `2`) it writes nothing.
After an `--update`, regenerate the wrapper if the change affects tools you call.

### What `check` does not touch

- It **never calls a tool** — `tools/list` only, no probing, no side effects on the server.
- It **never reads or writes** `<server>.shapes.json`. Your shape-spec and its hand-edits
  are untouched.

---

## Polymorphic tools (discriminated shaping)

Some tools return **different payload shapes** depending on an input argument
(e.g. `entityType=1` → `Person`, `entityType=2` → `Position`). A single flat
`return_model` would mistype every call but one. Use `discriminator` + `variants`
in the shape-spec instead:

```jsonc
{
  "get_entity": {
    "unwrap": ["data", "entity"],
    "discriminator": "entityType",          // input arg that selects the variant
    "input_overrides": { "entityType": "int" },
    "variants": {
      "1": { "return_model": "Person",   "fields": { "fullName": "str" } },
      "2": { "return_model": "Position", "fields": { "headline": "str" } }
    }
  }
}
```

Rules:
- Replace the flat `return_model`/`fields` with `discriminator` + `variants`.
- Variant keys are the discriminator **values as strings** (`"1"`, `"2"`, …).
- `unwrap` and `input_overrides` stay **top-level** — shared across all variants.

Codegen emits one `@overload` per variant (discriminator typed `Literal[<val>]`)
plus a union impl for all other cases:

```python
@overload
async def get_entity(caller: McpCaller, *, entityId: str, entityType: Literal[1]) -> Person: ...
@overload
async def get_entity(caller: McpCaller, *, entityId: str, entityType: Literal[2]) -> Position: ...
async def get_entity(caller: McpCaller, *, entityId: str, entityType: int) -> Person | Position:
    ...
```

**Call-site payoff** — a literal value lets the type checker narrow the return to
the exact variant; a runtime `int` widens to the union:

```python
me  = await mod.get_entity(caller, entityId="x", entityType=1)  # typed Person
pos = await mod.get_entity(caller, entityId="y", entityType=2)  # typed Position
```

Caveats: the discriminator is **always required** (even if the tool schema marks
it optional). An unmodeled discriminator value hits the `int` impl and returns the
union — it never raises.

### Enum params → `Literal[...]`

Any input param whose `inputSchema` carries an `enum` array is automatically typed as
`Literal[<v1>, <v2>, ...]` — no flag required. This applies to all scalar enums
(string, int, …). Use `mcpgen list <server> --schema` to inspect the raw `enum` arrays
before generating. Do not widen enum params to `str` in your calling code; if the server
accepts values outside the declared enum, the appropriate fix is to update the shape-spec
`input_overrides` for that param rather than loosening the call site.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Skill not listed in `/help` | Plugin not installed — run `/plugin marketplace add …` |
| `ReauthenticationRequired` | Run `mcpgen login <server>` |
| `PostLoginCheckFailed` | The token was issued and saved, but the check after login failed. Read the cause in the message — logging in again changes nothing |
| `TokenRefreshUnavailable` | The cached grant was not renewed, and the authorization server did not name the credential as the reason — a 5xx, a transport error, a `408`/`429`, a `temporarily_unavailable`/`server_error` code, a WAF block page, a `200` that is not a token, or a code faulting the request (`invalid_request`, `unsupported_grant_type`, `invalid_scope`). The message says which, and whether to retry or fix a configuration. It also names `mcpgen login <server>` wherever the cause is ambiguous enough that a fresh registration could still be the fix |
| `LoginWontHelp` | The base class of the two above. Catch it in batch code to abort on the first failure a browser round cannot fix |
| Config not found | Check the search order above; paths resolve from your cwd |
| Bearer token rejected | Confirm the env var is exported in the current shell |
| `uses SSE transport, which this mcpgen version does not support` | Only stdio and Streamable HTTP are implemented — see [§ Supported transports](#supported-transports) |
| `mcpgen check` exits `1` | The server's tools changed. Review the report, then regenerate, or `--update` to accept |
| `mcpgen check` exits `2` | Not drift — a missing/unreadable manifest, or a transport/auth/config failure. Read the `[check] error:` line on stderr |
