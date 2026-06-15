# Landscape: Python MCP clients + code-gen pattern (verified, mid-2026)

Source: deep-research workflow run `wf_8960abc0-585` (19 sources fetched, 95
claims extracted, 25 adversarially verified → 22 confirmed, 3 refuted).
All claims below are primary-source-backed unless flagged.

## TL;DR

- The "**call MCP from code, not from LLM context**" pattern is **vendor-endorsed**
  (Anthropic, Nov 2025) — exactly what you built into staffing-assistant.
- **Your reusable-client idea overlaps heavily** with the official `mcp` SDK and
  **FastMCP 2.x** — neither is a green field.
- **Your codegen-skill idea (generate per-server *typed importable Python wrapper
  source*) is a genuine gap.** Nearest competitor (mcp2py) runtime-proxies + emits
  `.pyi` stubs only; Anthropic's reference impl is **TypeScript-only**.
- Your specific differentiator — **persistent file token cache + proactive
  pre-flight refresh** — fills a real hole in the official SDK's canonical example
  and dodges token-cache bugs in FastMCP (one still open: #1764).

## Q1 — Existing Python MCP client libraries

| Library | Programmatic client | Streamable HTTP | OAuth 2.1 PKCE | Persistent token cache | Maturity |
|---|---|---|---|---|---|
| **official `mcp` SDK** | ✅ `ClientSession.call_tool` | ✅ `streamable_http_client` (was `streamablehttp_client`) | ✅ `OAuthClientProvider`+`TokenStorage` | ❌ canonical example is **in-memory only** — you implement `TokenStorage`. Refresh is **reactive** (on 401), not pre-flight | v1.x maintenance; **v2 beta ~2026-06-30 → API may shift** |
| **FastMCP 2.x** | ✅ `Client`, well-typed | ✅ (recommended for prod) | ✅ full: PKCE + DCR (RFC 7591) + CIMD + auto refresh-token | ✅ pluggable `token_storage` (AsyncKeyValue; encrypted-disk example) — **one open token-cache bug (#1764)**, see below | Most complete OAuth story; **3.0 beta exists** (scope findings to 2.x) |
| **mcp-use** | ✅ `MCPClient` — "direct tool calls without LLM" | ✅ | ❌ README documents **no** client OAuth/PKCE/token cache | Active; auth differentiator absent |
| **mcp2py** | ✅ runtime dynamic proxy (tools→functions) | ✅ (built on `streamablehttp_client`) | ❌ **no** OAuth+disk-token-refresh (claim REFUTED 0-3) | ❌ caches `.pyi` stubs only | latest 0.6.0 (2025-11-03); ~7mo stale |

**FastMCP token-cache caveat (re-verified 2026-06-15):** one confirmed-open
client auth bug — **#1764** (cached OAuth token not sent in subsequent requests
when multiple Client instances share the same cache dir). The originally-cited
cluster (#3425, #1863, #2641) is resolved: #3425 closed (behavior fixed in
fastmcp 3.2.0 via PR #3572 / issue #2862 — absolute `expires_at` stored);
#1863 closed (PR #2505 — refresh token now updates auth ContextVar);
#2641 closed (server-side JWT lifetime config, not a client-cache issue).
Soft signal, not proof.

## Q2 — Typed-wrapper code generation

- **Anthropic "Code execution with MCP"** (anthropic.com/engineering/code-execution-with-mcp,
  Nov 4 2025): present MCP servers as code APIs = per-tool wrapper files on a
  filesystem the agent calls from code. **Reference impl is TypeScript-only**
  (`servers/google-drive/getDocument.ts`, …); the word "Python" appears **zero
  times**. The article **never literally "recommends"** it — frames it as "one
  approach" whose savings "should be weighed against" sandboxing/monitoring/ops cost.
- **Cloudflare "Code Mode"** (blog.cloudflare.com/code-mode): same family — LLM
  writes code against generated TS bindings, executed in a Workers sandbox.
- **mcp2py**: nearest Python competitor, but **runtime dynamic proxy + `.pyi`
  stubs for IDE autocomplete only** — does NOT emit importable, reviewable,
  version-controllable `.py` wrapper modules.
- **No project emits per-server typed importable Python wrapper *source*.** ← your niche.

## Q3 — "Call MCP from code" best practice + pitfalls

- **Token savings**: Anthropic's headline **98.7%** (150k→2k tokens) is a *single
  illustrative example*, not a benchmark. Independent replication lands lower
  (~**78.5%**, 165k vs 771k input tokens). Mechanism (progressive disclosure +
  keeping intermediate results out of context) is real and corroborated.
- **Pitfalls**:
  - **Schema drift** — generated wrappers go stale vs updated tool signatures.
    Need a regenerate/diff/check mode. (Open question — not well-covered by sources.)
  - **Auth variance** — corporate OAuth-PKCE vs stdio vs API-key headers.
  - **Operational cost** — sandboxing/monitoring overhead "that direct tool calls
    avoid" (Anthropic's own caveat).
  - **Response-shape assumptions** — inputSchema describes inputs, not outputs;
    your staffing-assistant already learned this (empirical projection validation).

## Q4 — Distributing a small internal Python lib (2025-2026)

⚠️ **Weak coverage** — no surviving *verified claim* on this; below is from the
uv/astral primary docs that were fetched (distribution angle) + standard practice,
not adversarially verified. Treat as advisory.

- **git+https dependency via uv/pip** — lowest friction for a few colleagues.
  `uv add "git+https://git.example.com/…/mcp-client-kit.git@v0.1.0"`; uv supports git
  auth + tag/branch/rev pinning (docs.astral.sh/uv git auth). Good first step.
- **Private index** (internal PyPI / Artifactory / GitLab package registry) — when
  audience grows beyond a handful; `uv` supports extra indexes w/ auth.
- **copier/cookiecutter template** — orthogonal: scaffolds *new* projects, not how
  you ship *this* lib. Useful if the codegen-skill emits a whole project per server.
- **Recommendation**: ship as **git+https tag-pinned** now; promote to private
  index only if adoption justifies it. (Confirm with a dedicated search before
  committing — this question was under-researched.)

## Refuted / do-not-rely-on

1. ❌ mcp2py does OAuth w/ on-disk token cache+refresh at `~/.config/mcp2py/tokens.json` (0-3).
2. ❌ FastMCP #1764 is a "maintainer-confirmed" cross-instance cache bug (1-2 — "confirmed" was overstated; the issue IS open but not officially acknowledged by maintainers).
3. ❌ FastMCP caching "does not reliably persist across runs" (1-2 — it does persist when `token_storage` configured; #1764 is an edge case, not a general failure).

## Open questions carried forward

1. Does EPAM's OAuth need **pre-flight** refresh (short refresh-token life / odd
   expiry) or would FastMCP reactive-refresh + `token_storage` suffice? → decides
   whether your hand-rolled pre-flight is a true differentiator or redundant.
2. Real demand for **static importable** wrappers (diff/audit/IDE-without-runtime)
   over mcp2py's runtime proxy?
3. Distribution best practice (Q4) — needs its own research pass.
4. Schema-drift handling / stale-wrapper failure mode in the codegen pattern.
