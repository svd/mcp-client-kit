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
- FastMCP's *built-in* persistent cache has **at least one open token-cache bug
  (#1764 — cached token not sent in subsequent multi-instance requests)**. The
  originally-cited #3425 (expired-token-looks-fresh-after-reload) is **closed**;
  the behavior was fixed in fastmcp 3.2.0 (PR #3572). See §Correction below.

### Recommendation
- **Don't** publish "yet another MCP client". **Do** publish a focused
  **`PersistentOAuthStorage` + pre-flight-refresh helper** that plugs into *both*
  the official SDK (`TokenStorage`) and FastMCP (`token_storage` / AsyncKeyValue).
  Positioning: "the auth-persistence piece the SDK example omits", not "a client".
- ~~First, answer Open Question #1~~ **OQ#1 settled** — see §Fixed decisions #1.
  `_pre_flight_refresh` is load-bearing for the mcp SDK; the library stays as a thin
  auth-persistence helper in `_bridge.py`.
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
  range depending on workload). The origin project is a working, battle-tested case
  study — stages 1–4 already prove the payoff.
- Static generated source has properties the runtime-proxy crowd can't offer:
  **diff in PRs, audit, pin to a commit, IDE without a live server, validate
  response shapes empirically** (generated wrapper projections = the lesson encoded).

### Shape (from CODEGEN_SKILL_IDEA.md)
- **CLI does the 80% deterministic part** (`tools/list` → typed stubs) — belongs in
  the library, no LLM needed.
- **Skill does the 20% judgment part**: drive the CLI, probe live responses, edit
  wrappers to match *observed* shapes, curate which tools matter. This is the
  irreplaceable LLM-in-the-loop value and mirrors how the generated wrappers evolved.
- Add a **`--check` / drift mode** (re-list tools, diff vs generated) — directly
  answers the schema-drift pitfall.

### Risks
- Schema drift (mitigate: `--check` mode, CI-able).
- Response-shape assumptions from inputSchema alone (mitigate: mandatory empirical
  validation pass in the skill — this is the differentiator vs pure codegen).
- Auth variance — depends on Idea 1's client supporting OAuth + stdio + API-key.

---

## Combined recommendation (priority order)

1. ~~**Settle Open Question #1** (pre-flight-refresh necessity).~~ **Done** — see §Fixed decisions.
2. **Build Idea 2 (codegen skill + deterministic CLI)** — the novel, defensible part.
3. **Extract Idea 1 minimally** — as the auth-persistence helper the skill's generated
   code depends on, *not* as a standalone "MCP client". Adopt FastMCP underneath if
   QO#1 says reactive-refresh suffices.
4. **Distribute via git+https tag-pinned** (uv); private index later if adoption grows.
   (Re-research Q4 first — it was under-covered.)

**One-line answer:** the reusable *client* is mostly already built by others;
the *typed-Python-wrapper generator skill* is not — build that, and keep the auth
layer as a small focused dependency, not a flagship.

---

## Fixed decisions (2026-06-14 session) — do not re-litigate

1. **OQ#1 closed: the server supports reactive on-401 refresh** (long-lived
   reuse-tolerant tokens; RFC 8414 `token_endpoint` confirmed). However, the
   official `mcp` SDK never reaches its reactive-refresh path at cold start, so
   `_pre_flight_refresh` is **load-bearing** for the SDK client. The seam collapsed
   to a thin auth-persistence helper in `mcp_client_kit/_bridge.py`. See §Correction below.
2. **Auth for now: defer** *(historical — done; see §Correction).* The extraction and
   seam swap are complete: auth now lives in `mcp_client_kit/_bridge.py`.
3. **Architectural seam (locked):** generated wrappers MUST NOT import a concrete
   client — only the `McpCaller` Protocol in `mcp_client_kit/seam.py`. Swap
   the backend (`_bridge.py`) without regenerating wrappers. See §Correction for
   the backend choice (official mcp SDK, not FastMCP).
4. **Migration path:** auth done → prove skill → `--check` drift mode next.
   No flagship client.

---

## Correction (2026-06-1x session) — backend is the official SDK, NOT FastMCP

Earlier decisions/prompts said "swap the seam to **FastMCP**". The backend swap was
actually implemented on the **official `mcp` SDK** (`mcp[cli]>=1.27,<2`), not
FastMCP — `fastmcp` is not a dependency. This is the better choice (lighter,
already required transitively, avoids FastMCP's churn) — recorded here so it is
not re-litigated:

- **Backend = official `mcp` SDK** in `mcp_client_kit/_bridge.py`: `ClientSession`
  + `streamablehttp_client`/`stdio_client` + `OAuthClientProvider` + a hand-rolled
  `FileTokenStorage` (stores absolute `expires_at`) + out-of-band `_pre_flight_refresh`.
- **FastMCP issue #3425 is irrelevant here** and the code comment that cited it has
  been corrected. #3425 was a *FastMCP* token-cache bug (stale `expires_in` on
  reload), closed as a duplicate, fixed upstream in **fastmcp 3.2.0** (PR #3572).
  Our `FileTokenStorage` already stores absolute `expires_at`, so that class of bug
  cannot occur regardless of backend.
- **SETTLED:** `_pre_flight_refresh` is **load-bearing** — the mcp SDK 1.27.2
  never reaches its `refresh_token` grant path at cold start (`_initialize` skips
  `update_token_expiry`). Both `_pre_flight_refresh` and the `get_tokens` None-gate
  are kept unchanged — verified correct and necessary.
