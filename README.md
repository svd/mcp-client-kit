# mcp-client-kit (research / incubation)

Exploring whether to extract a reusable MCP client + build a Claude Code skill
that generates typed Python wrappers for any MCP server.

**Origin:** generalized from a prior internal project's hand-built MCP client
(OAuth 2.1 PKCE, file token storage, pre-flight refresh) and per-server wrappers —
the working prototype this builds on.

## Status: deterministic CLI prototype works (2026-06-14)

`mcp-kit codegen <server>` connects to a live MCP server, lists tools, and emits a
typed `async def` per tool against the `McpCaller` seam. Optional `--probe`
records a tool's real response *shape* — the empirical pass that beats pure
inputSchema codegen.

```
uv run mcp-kit codegen acme --out acme.py
uv run mcp-kit codegen acme --probe get_entity --probe-args '{"entityId":"…","entityType":1}'
```

`mcp-kit discover` lists MCP servers configured in installed agent hosts and prints a ready-to-run `mcp-kit list` command for each server it can connect to.

```
uv run mcp-kit discover
uv run mcp-kit discover --json
uv run mcp-kit discover --host claude-code
```

```
=== Claude Code ===

  codegraph              stdio      User config      Connected
    → mcp-kit list codegraph --stdio "codegraph serve --mcp"

  my-api                 http       User config      Needs authentication
    → mcp-kit list my-api --url https://example.com/mcp

  claude.ai Context7     http       User config      Connected
  ⚠  claude.ai connector — managed OAuth, not probeable by mcp-kit
```

Limitations:

- **claude.ai connectors** (e.g. Context7, Microsoft 365) appear in output but are marked non-probeable — they use managed OAuth that mcp-kit cannot replicate.
- **Fallback path** (when `claude` binary is absent): only reads `~/.claude.json`; plugin-provided servers and claude.ai connectors will be missing.

Not yet built: the skill layer (judgment pass — unwrap helpers, applying probe
findings, tool curation), `--check` drift mode. Auth is done:
`mcp_client_kit/_bridge.py` on the official `mcp` SDK (see VERDICT.md §Correction).

Read in this order:

1. **[`doc/VERDICT.md`](doc/VERDICT.md)** — should you build it? (TL;DR: skill yes, client mostly no) + Fixed decisions.
2. **[`doc/LANDSCAPE.md`](doc/LANDSCAPE.md)** — verified competitor landscape (mid-2026, 19 sources).
3. **[`doc/EXTRACTION_ANALYSIS.md`](doc/EXTRACTION_ANALYSIS.md)** — what's generic in `mcp_client.py`, API sketch, design debts.
4. **[`doc/CODEGEN_SKILL_IDEA.md`](doc/CODEGEN_SKILL_IDEA.md)** — design for the wrapper-generator skill + CLI split.

## One-line answer

The reusable *MCP client* overlaps heavily with the official `mcp` SDK — don't
reinvent it (and we haven't: `mcp_client_kit/_bridge.py` wraps it directly, plus
adds a load-bearing `_pre_flight_refresh`). The *typed-Python-wrapper generator*
(skill + deterministic CLI) is the genuine gap (Anthropic's pattern is TS-only;
mcp2py only runtime-proxies + emits `.pyi` stubs). **Build the skill; auth is a
small focused dependency, already done.**

## Auth: settled

`_pre_flight_refresh` is load-bearing — the official mcp SDK 1.27.2 never reaches
its silent-refresh path at cold start (`_initialize` skips `update_token_expiry`).
Fresh-process CLI re-auths in browser without it.

## Docs

- [`doc/USAGE.md`](doc/USAGE.md) — end-user guide (published flow: PyPI + marketplace); two paths: plugin/skill and CLI only.
- [`doc/RUNNING_LOCALLY.md`](doc/RUNNING_LOCALLY.md) — run from a local clone without installing.

## Next

Still deferred: skill layer (judgment pass — unwrap helpers, tool curation),
`--check` drift mode. Distribution: see `doc/DISTRIBUTION.md`.
