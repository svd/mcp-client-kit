# Session Report: generate-mcp-wrappers for codegraph

**Session ID:** `agent-a65b4abdc9e48dcb6`
**Date:** 2026-06-16 00:58 UTC
**Model:** claude-sonnet-4-6
**Context:** Workflow agent in `wf_721787cc-a68` (phase: Generate) inside session `c9f0c24b-604c-406c-aec0-52b3fe72187d` ("run-eval for all configured MCP servers")

---

## 1. Tool Calls Summary

### Generate Agent — `agent-a65b4abdc9e48dcb6` (37 total)

| # | Tool | Input summary | Attribution | Error? |
|---|------|--------------|-------------|--------|
| 1 | Skill | invoke `mcp-client-kit:generate-mcp-wrappers` for codegraph | workflow-driven (Generate phase) | — |
| 2 | Bash | `mcp-kit codegen codegraph --out codegraph/codegraph.py` (no transport) | skill-driven (step 1) | ERROR |
| 3 | Read | `servers/servers.toml` | LLM autonomous (error recovery) | — |
| 4 | Bash | `mcp-kit codegen --help` | LLM autonomous (error recovery) | — |
| 5 | Bash | `mcp-kit codegen codegraph --config servers/servers.toml` | LLM autonomous (error recovery) | ERROR |
| 6 | Bash | grep `_bridge.py` for config/transport hints | LLM autonomous (error recovery) | — |
| 7 | Read | `_bridge.py` offset 510 (open_session signature) | LLM autonomous (error recovery) | — |
| 8 | Read | `_bridge.py` offset 153 (_parse_servers) | LLM autonomous (error recovery) | — |
| 9 | Bash | `mcp-kit codegen codegraph --stdio "codegraph-mcp"` (wrong alias) | LLM autonomous (error recovery) | ERROR |
| 10 | Bash | `which codegraph-mcp` + `find` for binary | LLM autonomous (error recovery) | — |
| 11 | Bash | `find` for any `codegraph*` files | LLM autonomous (error recovery) | — |
| 12 | Bash | dump `settings.json` for MCP servers (returned empty `{}`) | LLM autonomous (error recovery) | — |
| 13 | Bash | `ls ~/.claude/` + read `claude_desktop_config.json` | LLM autonomous (error recovery) | — |
| 14 | Bash | dump `settings.json` grep for mcp/codegraph | LLM autonomous (error recovery) | — |
| 15 | Bash | `find` all Claude JSON files mentioning codegraph | LLM autonomous (error recovery) | — |
| 16 | Bash | extract codegraph entry from `~/.claude.json` | LLM autonomous (error recovery) | — |
| 17 | Bash | `which codegraph` → `/opt/homebrew/bin/codegraph` | LLM autonomous (error recovery) | — |
| 18 | Bash | `mcp-kit codegen codegraph --stdio "codegraph serve --mcp"` | LLM autonomous (error recovery) | — |
| 19 | Bash | `mcp-kit list codegraph --stdio "codegraph serve --mcp"` | skill-driven (step 2a) | — |
| 20 | Bash | probe `codegraph_status --args '{}'` | LLM autonomous (extra probe, non-listed tool) | — |
| 21 | Bash | probe `codegraph_search --args '{"query":"McpCaller"}'` | skill-driven (step 3) | — |
| 22 | Bash | probe `codegraph_context --args '{"query":"McpCaller","projectPath":"..."}'` | skill-driven (step 3) | — |
| 23 | Bash | probe `codegraph_node --args '{"symbol":"McpCaller"}'` | skill-driven (step 3) | — |
| 24 | Bash | probe `codegraph_explore --args '{"query":"McpCaller McpBridgeCaller"}'` | skill-driven (step 3) | — |
| 25 | Bash | probe `codegraph_trace --args '{"from":"McpCaller","to":"McpBridgeCaller"}'` | skill-driven (step 3) | — |
| 26 | Bash | `mcp-kit merge codegraph --out codegraph/codegraph.shapes.json` | skill-driven (step 3b) | — |
| 27 | Read | `codegraph/codegraph.shapes.json` (inspect observed shapes) | skill-driven (step 4) | — |
| 28 | Read | `codegraph/codegraph.py` (inspect generated stubs) | skill-driven (step 4) | — |
| 29 | Write | update `codegraph.shapes.json` (set return_model, scrub PII) | skill-driven (step 4) | — |
| 30 | Bash | `mcp-kit codegen codegraph --shapes codegraph.shapes.json --stdio "..."` | skill-driven (step 5) | — |
| 31 | Bash | `python3 -c "import ast; ast.parse(...)"` (AST verify) | skill-driven (step 5 verify) | — |
| 32 | Bash | `uv run eval-kit runner codegraph` (generate run.py) | workflow-driven (eval harness) | — |
| 33 | Read | `codegraph/run.py` (inspect generated runner) | workflow-driven (eval harness) | — |
| 34 | Write | `codegraph/session-overview.draft.md` | workflow-driven (eval harness artifact) | — |
| 35 | Bash | `ls -la codegraph/` (verify all 4 artifacts present) | LLM autonomous (verification) | — |
| 36 | Read | `codegraph/codegraph.py` (final review before output) | LLM autonomous (verification) | — |
| 37 | StructuredOutput | emit eval result JSON (`modes_hit=["A"]`, verdict_hint=pass) | workflow-driven (eval harness) | — |

**Errors: 3 out of 37 tool calls (8.1%) — all in the transport-discovery recovery loop (seq 2–18)**

---

## 2. Stages Executed

The generate-mcp-wrappers skill defines 5 steps. This is how they mapped onto the session:

| Stage | Seq | Outcome |
|-------|-----|---------|
| **Step 1** — Mechanical stubs (`mcp-kit codegen`) | 2, 18 | Failed first (no transport), succeeded on seq 18 after 15-step recovery |
| **Step 2** — List tools / select probes | 19 | `mcp-kit list` returned 5 tools; all non-mutating, all selected |
| **Step 3** — Probe + draft shape entries | 20–26 | 5 tools probed live + 1 autonomous extra (codegraph_status); merged at seq 26 |
| **Step 4** — Consistency pass / shape-spec edit | 27–29 | Read shapes + py, wrote updated shapes.json (return_model=null, PII scrubbed) |
| **Step 5** — Regenerate + verify | 30–31 | Regenerated with `--shapes`; AST parse: OK |
| **Eval harness extras** | 32–37 | runner, draft narrative, artifact listing, StructuredOutput |

---

## 3. Key Decisions Made

1. **Transport discovery (seq 3–18):** When `mcp-kit codegen codegraph` failed with no transport, the LLM did not stop — it explored `--help`, tried `--config`, read `_bridge.py` source, searched `find`, scanned `~/.claude/settings.json`, and finally read `~/.claude.json` where the correct invocation `{"command": "codegraph", "args": ["serve", "--mcp"]}` was stored. This led to the correct `--stdio "codegraph serve --mcp"`.

2. **All 5 tools probed (seq 21–25):** The LLM selected all non-mutating tools without an interactive gate (skill step 2b was implicitly satisfied since all tools are non-mutating and codegraph has only 5 tools). This was a sound judgment.

3. **Autonomous probe of codegraph_status (seq 20):** The LLM probed `codegraph_status` before listing tools — this tool was not in `mcp-kit list`'s output but was probed anyway (possibly from the MCP server's system prompt). The probe succeeded (returned node/edge counts).

4. **Shape-spec judgment (seq 27–29):** All tools return plain `str`. The LLM correctly set `return_model: null` for all entries (no TypedDict needed), kept `unwrap: []`, and scrubbed `probed_args` of the real project path. This was the correct minimal edit.

5. **Verdict: pass, modes_hit: ["A"]** (mode A = live stdio probe). All 5 tools were shaped; StructuredOutput noted "All tools return plain str (most return structured JSON as text)."

---

## 4. Errors and Recovery

### Error 1 — Bash seq 2: `mcp-kit codegen codegraph` with no transport

**What happened:** `mcp-kit codegen codegraph --out codegraph/codegraph.py` failed because no transport was specified and "codegraph" was not registered as a named HTTP server in `~/.mcp-client-kit/config.toml`.

**Error output:**
```
Exit code 1
+ Exception Group Traceback (most recent call last):
|   File ".../mcp-kit", line 10, in <module>
|     sys.exit(main())
```

**How the LLM recovered:** Read `servers/servers.toml` (seq 3) and `mcp-kit codegen --help` (seq 4), then tried `--config servers/servers.toml` (seq 5, also failed).

**Root cause and fix options:**
- Option A: The skill prompt should state that `mcp-kit codegen` requires `--stdio CMD` or `--url URL` when the server is not in `~/.mcp-client-kit/config.toml`. Stating this upfront would eliminate 8+ recovery steps.
- Option B: The eval harness could inject the `--stdio` flag from `servers.toml` directly into the agent's initial prompt.

---

### Error 2 — Bash seq 5: `mcp-kit codegen codegraph --config servers/servers.toml`

**What happened:** `--config` is not a recognized flag for `mcp-kit codegen`. It applies only to HTTP/OAuth named servers. Passing a local TOML with a stdio entry has no effect.

**Error output:**
```
Exit code 1
+ Exception Group Traceback (most recent call last): ...
```

**How the LLM recovered:** Grepped `_bridge.py` (seq 6) and read config-loading code (seq 7–8), concluding that TOML config does not feed `codegen` for stdio servers and that `--stdio CMD` must be specified explicitly.

**Root cause and fix options:**
- Option A: Document in `mcp-kit codegen --help` that `--config` does not apply for stdio servers.
- Option B: The eval harness prompt (server-eval-agent.md) could provide a transport hint so agents never try the wrong flag.

---

### Error 3 — Bash seq 9: `mcp-kit codegen codegraph --stdio "codegraph-mcp"` (wrong binary name)

**What happened:** `servers.toml` lists `codegraph-mcp` as the launch command, but the installed binary is `codegraph` invoked as `codegraph serve --mcp`. The alias `codegraph-mcp` does not exist on PATH.

**Error output:**
```
Exit code 1
Traceback (most recent call last): ...
```

**How the LLM recovered:** Ran `which codegraph-mcp` (not found), then `find` for `codegraph*` (seq 10–11), searched `~/.claude/settings.json` (seq 12–14), `find` all Claude JSON mentioning codegraph (seq 15), and finally read `~/.claude.json` (seq 16) where the correct invocation was found: `{"command": "codegraph", "args": ["serve", "--mcp"]}`. Verified with `which codegraph` → `/opt/homebrew/bin/codegraph` (seq 17). Succeeded on seq 18 with `--stdio "codegraph serve --mcp"`.

**Root cause and fix options:**
- Option A: Fix `servers.toml` to use `codegraph serve --mcp` as the `launch` value instead of `codegraph-mcp`. This single fix eliminates 9 extra tool calls.
- Option B: The eval harness could validate the `launch` command from `servers.toml` against PATH before dispatching the agent, and warn on mismatch.
- Option C: The skill could prescribe checking `~/.claude.json` mcpServers as the first fallback when the stated binary is not found — this is where Claude Code stores the authoritative invocation.

---

## 5. Token Usage and Cost

This report covers the **generate:codegraph** workflow agent only (`agent-a65b4abdc9e48dcb6`). For full workflow totals see the main session.

| Metric | generate:codegraph agent |
|--------|--------------------------|
| Input tokens | 70 |
| Output tokens | 7,552 |
| Cache writes | 85,614 |
| Cache reads | 2,071,164 |
| **Estimated cost** | **~$1.056** |

The extremely low raw-input / very high cache-read ratio (2 M cache reads vs. 70 direct input tokens) reflects a cache-warm context: the system prompt, skill content, and working state were served from the prompt cache on almost every turn. Output tokens (7,552) are modest given 37 tool calls across 62 turns.

### Cost by model

| Model | Sessions | Input | Output | Cache write | Cache read | Cost |
|-------|----------|-------|--------|-------------|------------|------|
| claude-sonnet-4-6 | 1 | 70 | 7,552 | 85,614 | 2,071,164 | ~$1.056 |

*Pricing at Sonnet 4.6 rates ($3/M input, $15/M output, $0.30/M cache-read, $3.75/M cache-write). Cache reads dominate cost.*

### Full workflow totals (wf_721787cc-a68, all phases, all servers)

| Metric | Total |
|--------|-------|
| Input tokens | 5,343 |
| Output tokens | 238,449 |
| Cache writes | 2,849,838 |
| Cache reads | 49,266,943 |
| **Estimated total cost** | **$29.06** |

The workflow ran 40 tracked agents across 53 transcript files (13 untracked from a prior resume run also counted in totals).

---

## 6. Skill vs. LLM Attribution

| Source | Tool calls | % |
|--------|-----------|---|
| `mcp-client-kit:generate-mcp-wrappers` (skill-driven steps 1–5) | 15 | 41% |
| workflow-driven (eval harness / Workflow tool prescription) | 5 | 13% |
| LLM autonomous — error recovery | 15 | 40% |
| LLM autonomous — verification / extras | 2 | 5% |

**Breakdown:**

- **Skill-driven (steps 1–5):** The initial `codegen` attempt (seq 2), `mcp-kit list` (seq 19), probes seq 21–25, `mcp-kit merge` (seq 26), shape read/edit/write (seq 27–29), regenerate + AST verify (seq 30–31).

- **Workflow-driven (eval harness):** Skill invocation (seq 1), runner generation (seq 32), runner review (seq 33), narrative draft (seq 34), StructuredOutput (seq 37).

- **LLM autonomous — error recovery (seq 3–18):** All 15 calls between the first failure (seq 2) and the successful codegen (seq 18). The skill does not prescribe how to resolve a missing-binary error. The LLM methodically explored: servers.toml, `--help`, `--config`, `_bridge.py` source code, filesystem searches, and multiple Claude config files — eventually finding the correct invocation in `~/.claude.json`.

- **LLM autonomous — verification/extras (seq 20, 35–36):** Probing `codegraph_status` (a tool not in the `mcp-kit list` output), directory listing, and final re-read of the generated module were not prescribed by the skill but were sensible.

Attribution is based on the skill's SKILL.md and observed call sequence. The skill does not cover transport-discovery logic, so all of seq 3–18 is classified as autonomous error recovery.

---

## 7. Optimization Recommendations

1. **Inject the correct `--stdio` command from `servers.toml` into the agent prompt.** The eval harness already has the `launch` field per server. Passing it as a one-liner hint (`Transport: stdio — use --stdio "codegraph serve --mcp"`) would have eliminated all 15 error-recovery calls (seq 3–18) and saved roughly half this session's output tokens (~3,750 output tokens).

2. **Fix the `launch` command mismatch in `servers.toml`.** The entry says `codegraph-mcp` but the binary is `codegraph serve --mcp`. This single discrepancy caused 3 failed Bash calls and 9 recovery steps involving filesystem searches and config file parsing. Fixing it in `servers.toml` costs one edit and prevents the same confusion in any future eval run or human-triggered wrapper generation.

3. **The skill should prescribe `~/.claude.json` mcpServers as the first transport fallback.** When `codegen` fails because the binary is not found, the right fallback is not `find` + filesystem scanning — it is reading `~/.claude.json` (the canonical Claude Code MCP config). Adding one line to SKILL.md ("If the stated launch command is not on PATH, check `~/.claude.json` mcpServers for the correct invocation") would reduce recovery from 9 steps to 2.

4. **The `codegraph_status` probe (seq 20) was an autonomous extra not listed by `mcp-kit list`.** The tool exists in the MCP server but was only discovered because the LLM apparently knew it from the codegraph server's context prompt. The skill should explicitly state: "Probe only tools that appear in the `mcp-kit list` output; do not add tools from other sources." This avoids probing tools the user did not approve.

5. **All responses were plain `str` — the shape-spec step reduced to a no-op.** For servers where all tools return unstructured text (no JSON envelopes), the skill's step 4 consists solely of confirming `return_model: null` across the board. A pre-check heuristic — if all observed shape `source` values are "live" and all `return_model` entries are null, emit a note and skip the Write cycle — would save the Read/Write round-trip (seq 27–29) at minimal risk.
