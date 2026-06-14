# mcp-client-kit (research / incubation)

Exploring whether to extract a reusable MCP client + build a Claude Code skill
that generates typed Python wrappers for any MCP server.

**Origin:** spun out of `a prior internal project`, whose
`the prior hand-built MCP client` (OAuth 2.1 PKCE, file token storage,
pre-flight refresh) and per-server wrappers (`radar.py`, `internal.py`) are the
working prototype this generalizes.

## Status: deterministic CLI prototype works (2026-06-14)

`mcp-kit codegen <server>` connects to a live MCP server, lists tools, and emits a
typed `async def` per tool against the `McpCaller` seam. Validated end-to-end
against corporate-internal MCP server (16 tools; generated wrappers run live). Optional `--probe`
records a tool's real response *shape* — the empirical pass that beats pure
inputSchema codegen. See `doc/EVAL_RADAR.md`.

```
uv run mcp-kit codegen radar --out radar.py
uv run mcp-kit codegen radar --probe get_entity --probe-args '{"entityId":"…","entityType":1}'
```

Not yet built: the skill layer (judgment pass — unwrap helpers, applying probe
findings, tool curation), `--check` drift mode. Auth is done:
`mcp_client_kit/_bridge.py` on the official `mcp` SDK (see VERDICT.md §Correction).

Read in this order:

1. **`doc/VERDICT.md`** — should you build it? (TL;DR: skill yes, client mostly no) + Fixed decisions.
2. **`doc/OQ1_PREFLIGHT.md`** — OQ#1 closed: mcp SDK needs pre-flight at cold start (§Removal eval); the server accepts reactive refresh but the SDK never reaches that path.
3. **`doc/EVAL_RADAR.md`** — the prototype's first eval against radar.
4. **`doc/LANDSCAPE.md`** — verified competitor landscape (mid-2026, 19 sources).
5. **`doc/EXTRACTION_ANALYSIS.md`** — what's generic in `mcp_client.py`, API sketch, design debts.
6. **`doc/CODEGEN_SKILL_IDEA.md`** — design for the wrapper-generator skill + CLI split.

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
Fresh-process CLI re-auths in browser without it. See `doc/OQ1_PREFLIGHT.md §Removal eval`.

## Next

Still deferred: skill layer (judgment pass — unwrap helpers, tool curation),
`--check` drift mode. Distribution: see `doc/DISTRIBUTION.md`.
