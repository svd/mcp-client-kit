# mcp-client-kit (research / incubation)

Exploring whether to extract a reusable MCP client + build a Claude Code skill
that generates typed Python wrappers for any MCP server.

**Origin:** spun out of `a prior internal project`, whose
`the prior hand-built MCP client` (OAuth 2.1 PKCE, file token storage,
pre-flight refresh) and per-server wrappers (`radar.py`, `internal.py`) are the
working prototype this generalizes.

## Status: research done, not yet built

Read in this order:

1. **`doc/VERDICT.md`** — should you build it? (TL;DR: skill yes, client mostly no)
2. **`doc/LANDSCAPE.md`** — verified competitor landscape (mid-2026, 19 sources).
3. **`doc/EXTRACTION_ANALYSIS.md`** — what's generic in `mcp_client.py`, API sketch, design debts.
4. **`doc/CODEGEN_SKILL_IDEA.md`** — design for the wrapper-generator skill + CLI split.

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
