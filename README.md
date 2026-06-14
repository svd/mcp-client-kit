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
findings, tool curation), `--check` drift mode, auth extraction (deferred — see
VERDICT.md "Fixed decisions").

Read in this order:

1. **`doc/VERDICT.md`** — should you build it? (TL;DR: skill yes, client mostly no) + Fixed decisions.
2. **`doc/OQ1_PREFLIGHT.md`** — the settled decisive unknown (needs no pre-flight refresh).
3. **`doc/EVAL_RADAR.md`** — the prototype's first eval against radar.
4. **`doc/LANDSCAPE.md`** — verified competitor landscape (mid-2026, 19 sources).
5. **`doc/EXTRACTION_ANALYSIS.md`** — what's generic in `mcp_client.py`, API sketch, design debts.
6. **`doc/CODEGEN_SKILL_IDEA.md`** — design for the wrapper-generator skill + CLI split.

## One-line answer

The reusable *MCP client* overlaps heavily with the official `mcp` SDK and
FastMCP 2.x — don't reinvent it. The *typed-Python-wrapper generator* (skill +
deterministic CLI) is a genuine gap (Anthropic's pattern is TS-only; mcp2py only
runtime-proxies + emits `.pyi` stubs). **Build the skill; keep auth as a small
focused dependency.**

## Decide-first

Before any code: does the OAuth actually need *pre-flight* refresh, or would
FastMCP's reactive auto-refresh + persistent `token_storage` suffice? That answer
collapses or justifies the whole "extract the client" half. See VERDICT §1.

## Next session

Restart in this project dir. Likely first tasks: (a) settle the pre-flight
question against the live server, (b) prototype the `tools/list → typed
stub` CLI generator, (c) re-research distribution (Q4 was under-covered).
