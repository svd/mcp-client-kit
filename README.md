# mcp-client-kit

**Write your MCP server wrappers once — from the live server. Keep them as real Python source you can diff, review, and pin.**

`mcpgen` turns an MCP server reachable over **stdio or Streamable HTTP** into a Python module: one `async def` per tool, typed inputs from the start, and typed returns once you've probed the real responses. No live server needed to read it.

`mcpgen` generates the Python wrapper layer for MCP code-mode architectures. Token savings occur when your agent or runtime executes these wrappers instead of loading the full MCP tool catalog into model context. `mcpgen` does not itself alter host tool registration and does not provide a sandbox.

> **Package / repo / plugin:** `mcp-client-kit` · **CLI command:** `mcpgen`
>
> Two artifacts, one repo: a **CLI** (`mcpgen`) you run anywhere, and a **Claude Code plugin** (`generate-mcp-wrappers` skill) that drives it for the parts that need judgment.

---

## The problem

MCP tool schemas eat your context window before the agent does any work.

Every tool definition costs **300–600 tokens** for its name, description, and JSON schema. That adds up fast:

- The **GitHub MCP server alone** burns ~55,000 tokens across its 93 tools.
- One developer measured **66,000 tokens consumed at conversation start** — a third of a 200k window, gone before the first query.
- A SaaS server with 50+ endpoints can spend **30,000+ tokens just describing what it *could* do.**

Anthropic's validated fix ("Code execution with MCP," Nov 2025): stop routing schemas through the model. Generate wrapper code and call the tools from code instead — an approach Anthropic measured shrinking one workflow from ~150,000 tokens to ~2,000 (**98.7%**), with independent benchmarks landing around **78–85%** on less extreme workloads.

Those numbers describe **the pattern**, as reported by Anthropic and by third-party benchmarks — they are not a measurement of this CLI. mcpgen supplies one piece of that pattern: the wrapper layer. What you save depends on how your runtime uses it.

The catch for Python teams: no good tool generated **standalone, importable, reviewable `.py` wrappers** from a live MCP server. So everyone hand-writes `jira.py`, `github.py`, `slack.py` — slowly, inconsistently, and they silently rot when the server changes.

That's the gap mcpgen fills.

---

## What you get

```bash
uv tool install mcp-client-kit          # puts `mcpgen` on your PATH
mcpgen login github                     # browser OAuth, tokens persisted
mcpgen codegen github --out github.py   # one async def per tool, typed inputs
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

The `-> GitHubUser` return type above comes from the full lifecycle — probe the tool, shape the response, regenerate. Straight out of `codegen`, `get_me` takes the same typed arguments but returns `Any`. See [Lifecycle](#lifecycle).

`github.py` is just Python. Open it in your IDE, review it in a PR, pin it to a commit, ship it. No runtime proxy, no framework lock-in, no live server required to read what your tools return.

**One connection for a series of calls.** Each call opens its own session by default. Wrap a run in `connected()` and every call to a server shares one initialized session — one `initialize()`, one stdio subprocess, one OAuth pre-flight refresh:

```python
caller = McpBridgeCaller(url="https://api.githubcopilot.com/mcp/")

async with caller.connected():
    me = await github.get_me(caller)
    issues = await github.list_issues(caller, owner="octocat", repo="hello-world")
```

Outside the block nothing changes, so existing code keeps working untouched. Details — per-server sessions, concurrency, cleanup — in [`doc/USAGE.md`](doc/USAGE.md#reusing-one-connection-for-a-series-of-calls).

---

## Why developers pick it

**Real source you own.** Importable `.py` modules — not `.pyi` stubs (mcp2py), not a runtime proxy, not tied to one execution framework (ipybox). You can diff it, review it, pin it, and read it in your IDE without a server running.

**Types that match reality.** A tool's `inputSchema` describes its *inputs* — it tells you nothing about the *output* shape, so `codegen` alone returns `Any`. mcpgen's `probe` makes a live call and records the actual response; you (or the skill) turn that observation into an output model, and regeneration bakes it in. Return types then reflect what the server really sends, not a guess. No other generator does this.

**OAuth that survives restarts.** Pre-flight token refresh means a fresh process renews a near-expired token silently from the refresh token — no surprise browser pop-up at cold start. (The official SDK's canonical example is in-memory only; every restart re-authenticates.)

**Swap auth without regenerating.** Every wrapper takes an `McpCaller` as its first argument. Change transports or auth backends — bearer, OAuth, stdio, a fake for tests — without touching the generated code.

**Built for production teams.** Works with MCP servers reachable over **stdio or Streamable HTTP** (OAuth, bearer/PAT, or no auth). Generated code lives in git like any other module, so it survives code review, audits, and pinning.

---

## What mcpgen does not do

- It does not remove tool schemas from Claude Code or Cursor context — host tool registration is not mcpgen's to change.
- It does not sandbox generated code. Generated wrappers are ordinary Python and run with your process's privileges.
- It does not infer trustworthy output types from input schemas alone. Output types come from live probes plus a judgment pass (see the lifecycle below).
- It does not speak SSE. SSE servers are surfaced by `mcpgen discover` but marked unsupported by this version.
- It does not guarantee a remote server will stay stable. That is what `mcpgen check` is for.

---

## Lifecycle

| Step | Command | Result |
|------|---------|--------|
| 1. Generate | `mcpgen codegen <server> --out <server>.py` | One `async def` per tool: typed inputs, `Any` return by default. |
| 2. Probe | `mcpgen probe <server> <tool> --args '{}' --emit-shape <server>.shapes.json` | Observed response-shape skeleton, written as a per-tool part under `<server>.shapes.json.parts/`. |
| 3. Merge | `mcpgen merge <server> --out <server>.shapes.json` | Consolidates the parts into the committed, hand-editable shape-spec. |
| 4. Edit the shape-spec (or run the skill) | edit `<server>.shapes.json` | Output model and `unwrap` decisions — the judgment pass. |
| 5. Regenerate | `mcpgen codegen <server> --out <server>.py --shapes <server>.shapes.json` | `TypedDict` returns, unions, lists, overloads. |

Step 3 is not optional: `--emit-shape` writes only into `<server>.shapes.json.parts/`, so `<server>.shapes.json` does not exist until you merge.

Typed input wrappers by default; empirically shaped output types after live probes. Steps 2–5 are optional as a group — skip them and you still get working wrappers, just with `Any` returns. Shaping is also per-tool: tools you never probe keep their `Any` return.

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
| `codegen <server>` | Emit wrappers; `--shapes` applies the shape-spec, `--probe` records a response shape inline, `--embed-schema` embeds `fn.__schema__` + Args docstring per function. Also writes `<out-stem>.mcpgen.json` for `check`. |
| `check <server>` | Compare the server's live tool inventory against the stored manifest. Exit `0` no drift, `1` drift, `2` operational error. |
| `list <server>` | Print a server's tools as JSON; `--schema` adds raw `inputSchema` per tool. |
| `probe <server> <tool>` | Live call(s) → response-shape skeleton. |
| `call <server> <tool> --out <p>` | One live call, raw payload to disk — bootstrap ids or inspect output. |
| `merge <server>` | Consolidate probe parts into `<server>.shapes.json`. |
| `login <server>` | Browser OAuth login; tokens stored at `~/.mcpgen/credentials.json`. |
| `migrate-creds` | Move stored OAuth tokens between `file` / `keyring` backends. |
| `discover` | List MCP servers configured in Claude Code. |

`discover` reads Claude Code's configuration — the only host provider implemented today. `discovery.PROVIDERS` is the extension point where more hosts get added.

Full workflow and flags: [`doc/USAGE.md`](doc/USAGE.md).

---

## Drift detection

`codegen` writes a deterministic, non-secret snapshot of the server's tool inventory
beside the generated module:

```bash
mcpgen codegen demo --stdio 'python server.py' --out gen/demo.py
# writes gen/demo.py and gen/demo.mcpgen.json
```

Commit both. In CI, verify the server still matches what you generated against:

```bash
mcpgen check demo --stdio 'python server.py' --manifest gen/demo.mcpgen.json
```

| Exit | Meaning |
|---|---|
| 0 | No drift. Description-only changes print as advisories and do not fail. |
| 1 | Drift: a tool was added, removed, or its input schema or annotations changed. |
| 2 | Operational error: manifest missing or unreadable, transport failure, auth failure. |

Exit 2 is never drift, so a broken runner or an expired token cannot be misread as a
changed tool contract. `--json` emits a structured report. `check` never calls a tool
and never touches `<server>.shapes.json`.

To accept the new inventory, ask for it explicitly — `check` never writes on its own:

```bash
mcpgen check demo --stdio 'python server.py' --manifest gen/demo.mcpgen.json --update
```

GitHub Actions:

```yaml
- name: Check MCP tool drift
  run: mcpgen check demo --config .mcp.json --manifest gen/demo.mcpgen.json --json
```

---

## Authentication

```bash
mcpgen login <server>                              # OAuth (most servers)
mcpgen login <server> --headless                   # no browser: print URL, paste callback URL back
mcpgen codegen <server> --bearer "$TOKEN" --out s.py  # PAT / bearer
mcpgen codegen <server> --stdio "python server.py" --out s.py  # local stdio, no auth
```

Tokens persist in `~/.mcpgen/credentials.json` (chmod 0600) or your OS keystore via `--cred-backend keyring`. In code, `ensure_login(server, url=...)` refreshes silently and only opens a browser when a real login is required; `ensure_login_all([...])` does the same for several servers, one at a time.

In containers, over SSH, or in CI there is no browser to open. `--headless` prints the authorization URL, you authorize on any device, and paste the callback URL back on stdin. It is auto-detected when neither `DISPLAY` nor `WAYLAND_DISPLAY` is set (never on macOS or Windows); `MCPGEN_HEADLESS=1`/`0` overrides the detection, and `--headless`/`--no-headless` overrides both.

---

## Who it's for

Python developers building AI agent pipelines on MCP servers — especially Claude Code users who've already hand-written at least one `<server>.py` wrapper and felt the pain. And platform teams running multi-server MCP environments where token cost and auth reliability are production concerns.

If you write your agent logic in Python and want generated tool wrappers you can actually own — review, pin, and keep in git — this is built for you.

---

## Docs

- [`doc/USAGE.md`](doc/USAGE.md) — full end-user guide: install paths, server config, auth, the shape-spec, and calling generated wrappers.
- [`doc/RUNNING_LOCALLY.md`](doc/RUNNING_LOCALLY.md) — run from a local clone without installing.

## Status

Early access (`v0.x`). The codegen engine, OAuth persistence, live-probe shaping, tool-inventory drift detection (`mcpgen check`), and both Claude Code skills are working today. Transport support is stdio and Streamable HTTP; SSE is discovered but not implemented.

## License

MIT.
