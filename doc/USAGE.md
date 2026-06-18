# mcp-client-kit usage

> **Availability:** this doc describes the published flow (PyPI + marketplace). Not yet
> live — for now use [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md).

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) — Claude Code users typically have it; otherwise
  `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Path A — Plugin / skill (recommended)

The plugin bundles the `generate-mcp-wrappers` skill. The skill drives the engine
automatically via `uvx` — no separate engine install needed.

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
1. Generates mechanical stubs (`mcpgen codegen`) for the target server.
2. Probes chosen tools live (`mcpgen probe`) to capture actual response shapes.
3. Regenerates wrappers with real return types.

Before the skill can reach your server, complete [§ Configure a server](#configure-a-server)
and [§ Authenticate](#authenticate).

For the full 6-step procedure see [§ The skill procedure](#the-skill-procedure).

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
| `codegen <server>` | Emit typed wrappers | `--out`, `--shapes <path>`, `--probe <tool>` / `--probe-args` |
| `list <server>` | Tools as JSON `[{name, description}]`; discriminator advisory on stderr | — |
| `probe <server> <tool>` | Live call(s) → shape skeleton | `--args` (repeatable), `--emit-shape <path>` (writes `.parts/`) |
| `call <server> <tool>` | One live call, raw payload to disk — bootstrap ids / inspect output | `--out <path>` (required) |
| `merge <server>` | Consolidate `.parts/` → `<server>.shapes.json`; emit gitignored `verify.json` | `--out <path>` |
| `login <server>` | Browser OAuth login | connection flags below |
| `migrate-creds` | Copy stored OAuth tokens between `file`/`keyring` backends | `--from`, `--to`, `--servers`, `--purge`, `--set-default` |
| `discover` | List servers from agent hosts | `--host <id>` (repeatable), `--json` |

Connection flags shared by `codegen`/`list`/`probe`/`call`/`login`: `--url`,
`--bearer`, `--stdio`, `--config`, `--client-name`, `--cred-backend`
(see [§ Authenticate](#authenticate)).

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

---

## Authenticate

**OAuth (most servers):**

```bash
mcpgen login myserver   # opens browser; token stored at ~/.mcpgen/credentials.json
```

Re-run when you see `ReauthenticationRequired`.

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
(`{"cred_backend": "..."}`), defaulting to `auto`.

| Backend | Storage |
|---------|---------|
| `file` | `~/.mcpgen/credentials.json` (chmod 0600) — works everywhere |
| `keyring` | OS native keystore (Keychain / Credential Locker / Secret Service); falls back to `file` with a warning if the keystore is unavailable |
| `auto` | Try `keyring`; if keystore is unavailable fall back to `file` silently — no warning (default) |

```bash
mcpgen login myserver --cred-backend keyring
```

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
`~/.mcpgen/config.json` → default (`auto`).

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
- **Lower-level alternative** — skip `ensure_login` and catch
  `ReauthenticationRequired` from the first failing `.call()`, then call
  `login(SERVER, url=URL)` and retry. `ReauthenticationRequired` and `login` are
  also exported from `mcpgen`.

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

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Skill not listed in `/help` | Plugin not installed — run `/plugin marketplace add …` |
| `ReauthenticationRequired` | Run `mcpgen login <server>` |
| Config not found | Check the search order above; paths resolve from your cwd |
| Bearer token rejected | Confirm the env var is exported in the current shell |
