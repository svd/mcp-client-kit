# mcpgen

**Write your MCP server wrappers once — from the live server. Keep them as real Python source you can diff, review, and pin.**

`mcpgen` turns any MCP server into a typed Python module: one `async def` per tool, real return types, no live server needed to read it. Call your tools from code instead of pumping their schemas through the model's context — the pattern Anthropic measured at up to **98% token reduction**.

> Two artifacts, one repo: a **CLI** (`mcpgen`) you run anywhere, and a **Claude Code plugin** (`generate-mcp-wrappers` skill) that drives it for the parts that need judgment.

---

## The problem

MCP tool schemas eat your context window before the agent does any work.

Every tool definition costs **300–600 tokens** for its name, description, and JSON schema. That adds up fast:

- The **GitHub MCP server alone** burns ~55,000 tokens across its 93 tools.
- One developer measured **66,000 tokens consumed at conversation start** — a third of a 200k window, gone before the first query.
- A SaaS server with 50+ endpoints can spend **30,000+ tokens just describing what it *could* do.**

Anthropic's validated fix ("Code execution with MCP," Nov 2025): stop routing schemas through the model. Generate wrapper code and call the tools from code instead — an approach Anthropic measured shrinking one workflow from ~150,000 tokens to ~2,000 (**98.7%**), with independent benchmarks landing around **78–85%** on less extreme workloads.

The catch for Python teams: no good tool generated **standalone, importable, reviewable `.py` wrappers** from a live MCP server. So everyone hand-writes `jira.py`, `github.py`, `slack.py` — slowly, inconsistently, and they silently rot when the server changes.

That's the gap mcpgen fills.

---

## What you get

```bash
uv tool install mcp-client-kit          # puts `mcpgen` on your PATH
mcpgen login github                     # browser OAuth, tokens persisted
mcpgen codegen github --out github.py   # typed wrappers for every tool
```

```python
import asyncio
from mcpgen import McpBridgeCaller
import github  # the file you just generated

async def main():
    caller = McpBridgeCaller(url="https://api.githubcopilot.com/mcp/")
    me = await github.get_me(caller)                                  # -> GitHubUser
    issues = await github.list_issues(caller, owner="octocat", repo="hello-world")
    print(me, issues)

asyncio.run(main())
```

`github.py` is just Python. Open it in your IDE, review it in a PR, pin it to a commit, ship it. No runtime proxy, no framework lock-in, no live server required to read what your tools return.

---

## Why developers pick it

**Real source you own.** Importable `.py` modules — not `.pyi` stubs (mcp2py), not a runtime proxy, not tied to one execution framework (ipybox). You can diff it, review it, pin it, and read it in your IDE without a server running.

**Types that match reality.** A tool's `inputSchema` describes its *inputs* — it tells you nothing about the *output* shape. mcpgen's `--probe` makes one live call and records the actual response, so your return types reflect what the server really sends. No other generator does this.

**OAuth that survives restarts.** Pre-flight token refresh means a fresh process renews a near-expired token silently from the refresh token — no surprise browser pop-up at cold start. (The official SDK's canonical example is in-memory only; every restart re-authenticates.)

**Swap auth without regenerating.** Every wrapper takes an `McpCaller` as its first argument. Change transports or auth backends — bearer, OAuth, stdio, a fake for tests — without touching the generated code.

**Built for production teams.** Works with any MCP server (HTTP URL, stdio, or bearer/PAT). Generated code lives in git like any other module, so it survives code review, audits, and pinning.

---

## How it works

| Step | Command | What happens |
|------|---------|--------------|
| 1. Generate | `mcpgen codegen <server> --out <server>.py` | One typed `async def` per tool. |
| 2. Probe (optional) | `mcpgen probe <server> <tool> --args '{}' --emit-shape <server>.shapes.json` | Records the *real* response shape from a live call. |
| 3. Regenerate | `mcpgen codegen <server> --out <server>.py --shapes <server>.shapes.json` | Wrappers now return precise types (`TypedDict`s, unions, lists). |

Polymorphic tools — ones that return different shapes depending on an input (`entityType=1` → `Person`, `=2` → `Position`) — get typed `@overload`s, so your type checker narrows the return at every call site.

The full reference, including the shape-spec format and credential backends, is in [`doc/USAGE.md`](doc/USAGE.md).

---

## Install

> The PyPI package is **`mcp-client-kit`**; the command it installs is **`mcpgen`**.

**CLI on your PATH:**

```bash
uv tool install mcp-client-kit
```

**One-off, no install:**

```bash
uvx --from mcp-client-kit mcpgen codegen <server> --out <server>.py
```

**As a project dependency:**

```bash
uv add mcp-client-kit      # or: pip install mcp-client-kit
```

Requires Python 3.11+.

---

## Claude Code plugin

The plugin bundles the `generate-mcp-wrappers` skill, which drives the CLI through the 20% that needs judgment — curating which tools matter, probing live responses, and editing the shape-spec — then regenerates and verifies the module.

The CLI is not bundled with the plugin — install it separately (`uv add mcp-client-kit`, see [Install](#install) above). The skill requires **mcpgen >= 0.1.0** and checks this before running; a local editable install (`uv pip install -e .`) satisfies it for development. This is a version floor, not an exact pin, so the skill and CLI can be upgraded independently as long as the CLI stays at or above the floor.

```
/plugin marketplace add svd/mcp-client-kit
/mcp-client-kit:generate-mcp-wrappers
```

A companion skill, `generate-mcp-runner`, writes a standalone smoke-test `run.py` that exercises the generated wrappers end-to-end.

---

## Command reference

| Command | What it does |
|---------|--------------|
| `codegen <server>` | Emit typed wrappers; `--shapes` applies the shape-spec, `--probe` records a response shape inline. |
| `list <server>` | Print a server's tools as JSON. |
| `probe <server> <tool>` | Live call(s) → response-shape skeleton. |
| `call <server> <tool> --out <p>` | One live call, raw payload to disk — bootstrap ids or inspect output. |
| `merge <server>` | Consolidate probe parts into `<server>.shapes.json`. |
| `login <server>` | Browser OAuth login; tokens stored at `~/.mcpgen/credentials.json`. |
| `migrate-creds` | Move stored OAuth tokens between `file` / `keyring` backends. |
| `discover` | List MCP servers configured in your installed agent hosts. |

Full workflow and flags: [`doc/USAGE.md`](doc/USAGE.md).

---

## Authentication

```bash
mcpgen login <server>                              # OAuth (most servers)
mcpgen codegen <server> --bearer "$TOKEN" --out s.py  # PAT / bearer
mcpgen codegen <server> --stdio "python server.py" --out s.py  # local stdio, no auth
```

Tokens persist in `~/.mcpgen/credentials.json` (chmod 0600) or your OS keystore via `--cred-backend keyring`. In code, `ensure_login(server, url=...)` refreshes silently and only opens a browser when a real login is required.

---

## Who it's for

Python developers building AI agent pipelines on MCP servers — especially Claude Code users who've already hand-written at least one `<server>.py` wrapper and felt the pain. And platform teams running multi-server MCP environments where token cost and auth reliability are production concerns.

If you write your agent logic in Python and want generated tool wrappers you can actually own — review, pin, and keep in git — this is built for you.

---

## Docs

- [`doc/USAGE.md`](doc/USAGE.md) — full end-user guide: install paths, server config, auth, the shape-spec, and calling generated wrappers.
- [`doc/RUNNING_LOCALLY.md`](doc/RUNNING_LOCALLY.md) — run from a local clone without installing.

## Status

Early access (`v0.x`). The codegen engine, OAuth persistence, live-probe shaping, and both Claude Code skills are working today. On the roadmap: `--check` drift mode, so CI can flag when a server's tools change out from under your wrappers.

## License

MIT.
