# Verdict: should you build this?

Two separate ideas. They have **very different** answers.

---

## Idea 1 — Extract the OAuth client as a reusable library

**Verdict: ⚠️ Partial. Do it, but scope it down and don't oversell it.**

### Against (be honest)
- The official `mcp` SDK already gives `ClientSession`, Streamable HTTP, and
  `OAuthClientProvider`+`TokenStorage`. **FastMCP 2.x** already ships PKCE + DCR +
  CIMD + auto-refresh + **pluggable persistent `token_storage`**. Your core is
  **not** green field — most of it is a thin convenience layer over things that exist.
- For colleagues who don't need OAuth (your own question): the value shrinks to
  "one-line `call()` + envelope parse + session mgmt + a login CLI". That's a nice
  helper, not a compelling library — FastMCP's `Client` covers it.

### For (the genuine slice)
- **Persistent file token storage + proactive pre-flight refresh** is a real gap in
  the official SDK's canonical example (in-memory only; reactive refresh on 401).
- FastMCP's *built-in* persistent cache has **open token-cache/refresh bugs in 2026**
  (esp. #3425 — expired-token-looks-fresh-after-reload, the exact failure your
  pre-flight refresh prevents). So a small, **correct** persistent-auth layer has
  value *today* — but it's a window that may close as FastMCP fixes those bugs.

### Recommendation
- **Don't** publish "yet another MCP client". **Do** publish a focused
  **`PersistentOAuthStorage` + pre-flight-refresh helper** that plugs into *both*
  the official SDK (`TokenStorage`) and FastMCP (`token_storage` / AsyncKeyValue).
  Positioning: "the auth-persistence piece the SDK example omits", not "a client".
- **First, answer Open Question #1**: does EPAM actually need pre-flight refresh, or
  would FastMCP reactive-refresh + `token_storage` suffice? If FastMCP suffices →
  the library shrinks to near-zero and you should just adopt FastMCP. **This is the
  single most decision-relevant unknown — settle it before writing extraction code.**
- Fix the 4 design debts from EXTRACTION_ANALYSIS.md during extraction (plaintext
  tokens → keyring/chmod, SDK-internals reach → RFC 8414 discovery fallback, public
  storage API, session-reuse).

---

## Idea 2 — Claude Code skill that generates typed Python wrappers per MCP server

**Verdict: ✅ Worth it. This is the actually-novel part.**

### Why
- **No existing project emits per-server typed *importable Python wrapper source*.**
  - Anthropic's reference pattern: **TypeScript only** (0 mentions of Python).
  - Cloudflare Code Mode: TS bindings in a Workers sandbox.
  - mcp2py (nearest): **runtime dynamic proxy + `.pyi` stubs only** — no reviewable,
    diffable, version-controllable `.py` modules.
- The pattern is **vendor-endorsed** and the **token-savings are real** (78–99%
  range depending on workload). Your own staffing-assistant is a working,
  battle-tested case study — stages 1–4 already prove the payoff.
- Static generated source has properties the runtime-proxy crowd can't offer:
  **diff in PRs, audit, pin to a commit, IDE without a live server, validate
  response shapes empirically** (your `radar.py` projections = the lesson encoded).

### Shape (from CODEGEN_SKILL_IDEA.md)
- **CLI does the 80% deterministic part** (`tools/list` → typed stubs) — belongs in
  the library, no LLM needed.
- **Skill does the 20% judgment part**: drive the CLI, probe live responses, edit
  wrappers to match *observed* shapes, curate which tools matter. This is the
  irreplaceable LLM-in-the-loop value and mirrors how `radar.py` actually evolved.
- Add a **`--check` / drift mode** (re-list tools, diff vs generated) — directly
  answers the schema-drift pitfall.

### Risks
- Schema drift (mitigate: `--check` mode, CI-able).
- Response-shape assumptions from inputSchema alone (mitigate: mandatory empirical
  validation pass in the skill — this is the differentiator vs pure codegen).
- Auth variance — depends on Idea 1's client supporting OAuth + stdio + API-key.

---

## Combined recommendation (priority order)

1. **Settle Open Question #1** (EPAM pre-flight-refresh necessity). Cheap, decisive.
2. **Build Idea 2 (codegen skill + deterministic CLI)** — the novel, defensible part.
   Use staffing-assistant as the reference/eval case.
3. **Extract Idea 1 minimally** — as the auth-persistence helper the skill's generated
   code depends on, *not* as a standalone "MCP client". Adopt FastMCP underneath if
   QO#1 says reactive-refresh suffices.
4. **Distribute via git+https tag-pinned** (uv); private index later if adoption grows.
   (Re-research Q4 first — it was under-covered.)

**One-line answer:** the reusable *client* is mostly already built by others;
the *typed-Python-wrapper generator skill* is not — build that, and keep the auth
layer as a small focused dependency, not a flagship.
