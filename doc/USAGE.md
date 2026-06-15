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
/plugin marketplace add <owner>/mcp-client-kit
```

Or via the `svd-agent-skills` aggregator if it's listed there.

**Invoke the skill** in any Claude Code session:

```
/mcp-client-kit:generate-mcp-wrappers
```

The skill:
1. Generates mechanical stubs (`mcp-kit codegen`) for the target server.
2. Probes chosen tools live (`mcp-kit probe`) to capture actual response shapes.
3. Regenerates wrappers with real return types.

Before the skill can reach your server, complete [§ Configure a server](#configure-a-server)
and [§ Authenticate](#authenticate).

For the full 6-step procedure see [§ The skill procedure](#the-skill-procedure).

---

## Path B — CLI only

Use `mcp-kit` directly to generate wrappers or probe tools without the skill layer.

### One-off (no install)

```bash
# Note: package = mcp-client-kit, script = mcp-kit — they differ
uvx --from "mcp-client-kit" mcp-kit codegen <server> --out <server>.py
uvx --from "mcp-client-kit" mcp-kit probe <server> <tool> --args '{}' --emit-shape <server>.shapes.json
uvx --from "mcp-client-kit" mcp-kit login <server>
```

### Persistent (on PATH)

```bash
uv tool install mcp-client-kit   # installs mcp-kit on PATH
mcp-kit codegen <server> --out <server>.py
```

### Project dependency

```bash
uv add mcp-client-kit            # or: pip install mcp-client-kit
```

---

## Configure a server

Config resolves relative to your **current working directory**. Search order (first match wins):

1. `--config <path>` — explicit override
2. `$MCP_KIT_SERVERS` env var — path to a JSON file
3. `~/.mcp-client-kit/servers.json` — user-global fallback
4. `./.mcp.json` in cwd — Claude Code format

Copy the bundled template and edit:

```bash
cp <kit-root>/servers.example.json .mcp-servers.json
export MCP_KIT_SERVERS=.mcp-servers.json
```

Both formats are accepted:

```jsonc
// Simple: name → URL
{ "myserver": "https://mcp.example.com/mcp/v1" }

// Claude Code format
{ "mcpServers": { "myserver": { "url": "https://mcp.example.com/mcp/v1", "clientName": "my-app" } } }
```

---

## Authenticate

**OAuth (most servers):**

```bash
mcp-kit login myserver   # opens browser; token stored at ~/.mcp-client-kit/credentials.json
```

Re-run when you see `ReauthenticationRequired`.

**PAT / Bearer token:**

```bash
export MYSERVER_TOKEN="pat_..."
mcp-kit codegen myserver --bearer "$MYSERVER_TOKEN" --out myserver.py
```

Never pass a literal token on the command line; always read from an env var.

**Local stdio server (no auth):**

```bash
mcp-kit codegen myserver --stdio "python path/to/server.py" --out myserver.py
```

---

## The skill procedure

Six steps from invocation to typed wrappers:

| Step | What it does |
|------|-------------|
| 1. Mechanical stubs | `mcp-kit codegen <server> --out <server>.py` — all tools, returns `Any` |
| 2. Curate | Pick tools whose payloads you want typed (not all of them) |
| 3. Probe → skeleton | `mcp-kit probe <server> <tool> --args '...' --emit-shape <server>.shapes.json` |
| 4. Edit shape-spec | Set `unwrap`, `return_model`, `fields`, `input_overrides` — the judgment pass |
| 5. Regenerate | `mcp-kit codegen <server> --out <server>.py --shapes <server>.shapes.json` |
| 6. Verify | `ast.parse` the module; confirm return types |

Multi-probe: repeat `--args` for each sample input; shapes are deep-merged (keys unioned,
type conflicts widened). Use when fields are nullable or a tool has multiple response shapes.

**Security:** `probe` records live call arguments verbatim in `<server>.shapes.json`. Before
committing, scrub `probed_args` — replace real ids/names/PII with placeholders like
`"<example-id>"`. Real values survive deletion via git history.

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
from mcp_client_kit import McpBridgeCaller
import github  # generated: mcp-kit codegen github --out github.py --bearer "$GITHUB_TOKEN"

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

**OAuth example** (server logged in via `mcp-kit login`):

```python
caller = McpBridgeCaller(url="https://mcp.example.com/mcp/v1")
# stored token is picked up automatically from ~/.mcp-client-kit/credentials.json
```

`McpBridgeCaller` kwargs mirror the CLI connection flags — `url=`, `bearer=` (PAT),
`cmd=` (stdio), `config_path=`, `client_name=`. One instance is reusable across
multiple calls and multiple servers (the `SERVER` constant is baked into each
generated module, not into the caller).

For typing your own caller (e.g. in tests), implement `McpCaller`:

```python
from mcp_client_kit import McpCaller
from typing import Any

class FakeCaller:
    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        return {"login": "octocat"}
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Skill not listed in `/help` | Plugin not installed — run `/plugin marketplace add …` |
| `ReauthenticationRequired` | Run `mcp-kit login <server>` |
| `uvx …` fails: `No such command 'mcp-kit'` | Use `uvx --from "mcp-client-kit" mcp-kit …`; the script name differs from the package name |
| Config not found | Check the search order above; paths resolve from your cwd |
| Bearer token rejected | Confirm the env var is exported in the current shell |
