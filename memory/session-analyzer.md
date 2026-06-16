# Session Report: generate-mcp-wrappers — memory server

**Session ID:** `c9f0c24b-604c-406c-aec0-52b3fe72187d`
**Date:** 2026-06-16 (00:55 UTC)
**Model:** claude-sonnet-4-6
**Scope:** This report focuses on the `generate:memory` agent within the `run-eval` workflow, plus the downstream `analyze:memory` and `verify:memory` agents that handled the memory server evaluation.

---

## 1. Tool Calls Summary

### Main Session (8 total)

The main session bootstrapped the eval and launched the workflow. It made 8 tool calls before handing off to the `run-eval` workflow.

| # | Tool | Count | Attribution |
|---|------|-------|-------------|
| 1 | Read | 3 | LLM autonomous (context gathering) |
| 2 | Bash | 3 | LLM autonomous (discovery) |
| 3 | Workflow | 2 | skill-driven (run-eval.workflow.js launch + resume) |

The two `Workflow` calls correspond to an initial launch and a resume (`resumeFromRunId: wf_721787cc-a68`), indicating the workflow was interrupted and restarted.

### Workflow: run-eval (wf_721787cc-a68)

| Workflow | Status | Agents (tracked) | Transcript files | Tool calls | Errors | Total cost |
|----------|--------|------------------|------------------|-----------|--------|------------|
| run-eval | completed | 40 | 59 | 1,015 | ~135 | $34.07 |

The gap between 40 tracked agents and 59 transcript files reflects a prior aborted run — 19 untracked agents from the first run are counted in token spend but show as `(prior-run/untracked)` in the phase rollup.

#### Phase rollup (run-eval workflow)

| Phase | Agents | Tool calls | Errors |
|-------|--------|-----------|--------|
| Generate | 15 | 431 | 47 |
| Analyze | 12 | 368 | 42 |
| Verify | 12 | 32 | 0 |
| Report | 1 | 0 | 0 |
| (untracked prior run) | 19 | 511 | 46 |

### memory-specific agents

| Agent | Phase | Turns | Tool calls | Errors |
|-------|-------|-------|-----------|--------|
| generate:memory | Generate | 71 | 46 | 5 |
| analyze:memory | Analyze | 43 | 35 | 5 |
| verify:memory | Verify | 6 | 4 | 0 |

#### generate:memory — Tool calls detail (46 total)

| Tool | Count | Attribution |
|------|-------|-------------|
| Bash | 26 | skill-driven (mcp-kit codegen, probe, list, call, merge; eval-kit runner) |
| Read | 16 | LLM autonomous (config discovery, source inspection) + skill-driven (shapes inspection) |
| Write | 2 | skill-driven (shapes.json update) |
| Skill | 1 | skill-driven (invoke generate-mcp-wrappers) |
| StructuredOutput | 1 | workflow-driven (phase completion contract) |

#### analyze:memory — Tool calls detail (35 total)

| Tool | Count | Attribution |
|------|-------|-------------|
| Bash | 28 | LLM autonomous (session parser invocations, data extraction) |
| Read | 5 | LLM autonomous (session-overview.draft.md, skill reference files) |
| Write | 1 | skill-driven (write session-analyzer.md) |
| Skill | 1 | skill-driven (invoke session-analyzer) |

#### verify:memory — Tool calls detail (4 total)

| Tool | Count | Attribution |
|------|-------|-------------|
| Bash | 3 | workflow-driven (eval-kit merge-session, verify, runner) |
| StructuredOutput | 1 | workflow-driven (phase completion contract) |

---

## 2. Skill vs. LLM Attribution

### generate:memory (46 tool calls)

| Source | Tool calls | % |
|--------|-----------|---|
| mcp-client-kit:generate-mcp-wrappers (skill-prescribed) | 30 | 65% |
| LLM autonomous | 16 | 35% |

The skill prescribed: `mcp-kit codegen`, `mcp-kit list`, `mcp-kit probe`, `mcp-kit merge`, `mcp-kit call`, `eval-kit runner`, and shapes.json authoring. The LLM autonomously handled: config discovery (searching for `servers.toml`, reading `.mcp.json`), inspecting the `_bridge.py` source to understand config loading precedence, cross-referencing completed peers (`filesystem/`, `time/`, `sqlite/`) to confirm the correct working directory, and AST-validating the output.

Attribution is inferred from the SKILL.md at `/Users/Sviataslau_Svirydau/src/mcp-client-kit/skills/generate-mcp-wrappers/SKILL.md`.

### analyze:memory (35 tool calls)

| Source | Tool calls | % |
|--------|-----------|---|
| session-analyzer (skill-prescribed) | 6 | 17% |
| LLM autonomous | 29 | 83% |

The skill prescribed invoking the parser script and writing the final report. The remaining 83% was LLM exploration: repeated parse + filter bash pipelines, probing the JSONL structure directly, and reading draft files to synthesize the narrative.

### verify:memory (4 tool calls)

| Source | Tool calls | % |
|--------|-----------|---|
| workflow-driven | 4 | 100% |

All 4 calls were prescribed by the `run-eval` workflow's Verify phase contract.

---

## 3. Errors and Recovery

### Error 1 — Bash (seq 2, generate:memory) — Wrong working directory for codegen

**What happened:** The agent cd'd into `/Users/Sviataslau_Svirydau/src/mcp-client-kit` and ran `uv run mcp-kit codegen memory --out memory/memory.py`, but the mcp-kit venv in that directory could not resolve the `memory` server — the server config lived in the eval repo, not in the mcp-client-kit source repo. The call raised an `ExceptionGroup` from async task cancellation.

**Error output:**
```
Exit code 1
  + Exception Group Traceback (most recent call last):
  |   File ".../.venv/bin/mcp-kit", line 10, in <module>
  |     sys.exit(main())
```

**How the LLM recovered:** Seqs 3–6: tried to read `servers.toml` from the mcp-client-kit repo (also failed), then used `find` across both repos, located the correct config at `mcp-client-kit-eval/servers/servers.toml`, and read its contents to extract the transport command.

**Root cause and fix options:**
- Option A: The workflow agent prompt should explicitly state: "CWD is the eval repo; always invoke `uv run mcp-kit` from the eval repo's venv, not from mcp-client-kit source."
- Option B: Add a project-level `MCP_KIT_SERVERS` env var pointing to `servers/servers.toml` so the tool auto-discovers it.

---

### Error 2 — Read (seq 3, generate:memory) — Config file not in mcp-client-kit

**What happened:** After the bash failure the agent tried to `Read` `/Users/Sviataslau_Svirydau/src/mcp-client-kit/servers/servers.toml`, which does not exist.

**Error output:**
```
File does not exist. Note: your current working directory is /Users/Sviataslau_Svirydau/src/mcp-client-kit-eval.
```

**How the LLM recovered:** The error message's CWD hint was the recovery signal — agent used `find` (seqs 4–5) to locate the correct path and read it (seq 6).

**Root cause:** Same as Error 1 — incorrect repo assumed for config location.

---

### Error 3 — Bash (seq 7, generate:memory) — mcp-kit list without --stdio flag

**What happened:** `uv run mcp-kit list memory` (no transport flag) failed because no server config was auto-discovered in the eval repo's context.

**Error output:**
```
Exit code 1
  + Exception Group Traceback ...
```

**How the LLM recovered:** Seqs 8–22: read `mcp-kit --help`, read `.mcp.json`, read `_bridge.py` source to understand config discovery order (env var → `~/.mcp-client-kit/servers.json` → explicit flags), then correctly used `--stdio "npx -y @modelcontextprotocol/server-memory"` (seq 23). Successful on first correct attempt.

**Root cause and fix options:**
- The skill prompt should explicitly state the `--stdio` flag pattern for stdio servers so agents do not need to read framework source to discover this.

---

### Error 4 — Bash (seq 12, generate:memory) — --config flag not supported on list

**What happened:** After finding `servers.toml` the agent tried `mcp-kit list memory --config /path/to/servers.toml`, but the `--config` flag is not implemented on the `list` subcommand (only on `codegen`).

**Error output:**
```
Exit code 1
  + Exception Group Traceback ...
```

**How the LLM recovered:** Read `_bridge.py` source (seqs 13–15) to confirm config resolution, then used `--stdio` directly. Same correct invocation as seq 23.

**Root cause and fix options:**
- Option A: Document in the skill that `--config` is only supported on `codegen` — `list`, `probe`, and `call` require explicit `--stdio` or `--url`.
- Option B: Implement `--config` consistently across all subcommands.

---

### Error 5 — Bash (seq 28, generate:memory) — probe open_nodes exits 1 but writes parts file

**What happened:** `mcp-kit probe memory open_nodes --args '{"names": ["example-node"]}'` probed a node that does not exist in the (empty) knowledge graph. The server responded but returned an empty/error payload; `mcp-kit probe` exited with code 1. The parts file was still written to `memory/memory.shapes.json.parts/open_nodes.json`.

**Error output:**
```
Exit code 1
[probe] memory.open_nodes (1 probe(s)) …
[probe]   [1/1] args={'names': ['example-node']}
Knowledge Graph MCP Server running on stdio
[probe] wrote part memory/memory.shapes.json.parts/open_nodes.json
```

**How the LLM recovered:** Seq 29: read the parts file and confirmed it contained valid shape data. Continued normally to the merge step. The exit code 1 was effectively a false negative — the probe succeeded in capturing shape structure.

**Root cause and fix options:**
- Option A: `mcp-kit probe` should exit 0 when shape data is captured even if the server returned an error payload. Reserve exit 1 for transport failures.
- Option B: Document in the skill: "probe exit 1 with a written parts file = server responded with empty/error data — shape is still captured; check the parts file before deciding to retry."

---

### analyze:memory Errors (5 errors, seqs 18, 19, 27, 28, 30)

All five were bash pipeline failures during the analysis phase:

- **Seqs 18–19**: Python one-liners filtering the parsed JSON silently returned nothing because the output format differed from expected (the parser output includes both dict and int entries in the agents list). Recovery: reformulated the filter with `isinstance` guard.
- **Seq 27**: Attempted to read skill reference files from `/src/agent-skills/plugins/mcp-client-kit/skills/generate-mcp-wrappers` — path was wrong (no `plugins/` in the actual structure). Recovery: used `find` (seq 29) to locate the SKILL.md.
- **Seq 28**: Python script crashed with `AttributeError: 'int' object has no attribute 'get'` when iterating the agents list (same int-entry issue). Recovery: added type guard.
- **Seq 30**: Another data extraction attempt that produced no output. Recovery: switched to reading `session-overview.draft.md` directly (seq 31) and synthesized the report from that.

---

## 4. Token Usage and Cost

### memory-specific agents

| Metric | generate:memory | analyze:memory | verify:memory | Memory subtotal |
|--------|----------------|----------------|---------------|-----------------|
| Input tokens | 141 | 2,013 | 10 | 2,164 |
| Output tokens | 7,245 | 10,551 | 626 | 18,422 |
| Cache writes | 80,701 | 70,954 | 10,174 | 161,829 |
| Cache reads | 2,483,286 | 1,264,939 | 58,482 | 3,806,707 |
| **Estimated cost** | ~$1.49 | ~$0.76 | ~$0.04 | **~$2.29** |

*Estimated at claude-sonnet-4-6 rates: $3/MTok input, $15/MTok output, $3.75/MTok cache write, $0.30/MTok cache read.*

### Full session (all servers + main session)

| Metric | Main session | Workflow (all agents) | Total |
|--------|-------------|----------------------|-------|
| Input tokens | 631 | 5,762 | 6,393 |
| Output tokens | 6,944 | 272,976 | 279,920 |
| Cache writes | 145,992 | 3,210,913 | 3,356,905 |
| Cache reads | 607,123 | 56,946,753 | 57,553,876 |
| **Estimated cost** | ~$0.07 | ~$33.80 | **$34.07** |

### Cost by model

| Model | Sessions | Input | Output | Cache write | Cache read | Cost |
|-------|----------|-------|--------|-------------|------------|------|
| claude-sonnet-4-6 | 51 | 6,393 | 279,920 | 3,356,905 | 57,553,876 | $34.07 |
| \<synthetic\> | 9 | 0 | 0 | 0 | 0 | — |

**Cost distribution note:** Cache reads (57.5M tokens at $0.30/MTok) account for $17.27 (51% of total). Cache writes (3.4M tokens at $3.75/MTok) account for $12.59 (37%). Fresh input/output tokens are under $5. This confirms the workflow is cache-heavy by design.

---

## 5. Stages Executed (memory server — generate:memory)

The generate:memory agent executed these stages of the `generate-mcp-wrappers` skill:

| Stage | Seq(s) | Status | Notes |
|-------|--------|--------|-------|
| 1. Mechanical stubs (codegen) | 2, 24 | 3 attempts; success at seq 24 | Wrong repo + missing --stdio resolved autonomously |
| 2. Tool selection | — | Implicit "probe all readable" | No interactive gate (subagent; no AskUserQuestion) |
| 3. Live probe — read_graph | 26 | Success first try | `--stdio` flag applied correctly after earlier recovery |
| 3. Live probe — search_nodes | 27 | Success first try | |
| 3. Live probe — open_nodes | 28 | Exit 1 but parts written | False negative; shape captured |
| 3b. Merge parts | 32 | Success | 3 tools merged |
| 4. Shape inspection | 29–35 | Success | Read parts, called read_graph live, understood envelope |
| 5. Regenerate with shapes | 37 | Success | KnowledgeGraph TypedDict emitted for 3 tools |
| 6. Verify | 39, 44 | Success | AST parse OK; 9 async defs, 3 KnowledgeGraph return models |
| eval-kit runner | 40 | Success | run.py written on first attempt |
| Draft narrative | 42 | Success | session-overview.draft.md written |
| StructuredOutput | 46 | Pass | verdict_hint=pass, shaped_tools=[read_graph, search_nodes, open_nodes] |

The skill's prescribed parallel-subagent dispatch (for batches of probes) was not used — all probes ran inline. This is within the skill's single-thread allowance for servers with ≤4 selected tools.

---

## 6. Optimization Recommendations

1. **Fix the "wrong repo / wrong venv" first-call failure to save ~10 tool calls per server.** Every generate agent in this eval burned seqs 2–22 discovering which repo and venv to use. The workflow agent prompt should state explicitly: "CWD is the eval repo; invoke `uv run mcp-kit` without cd-ing; server transport is `--stdio <cmd>` extracted from `servers/servers.toml`; `--config` is not supported on `list`/`probe`." This pattern failed identically in generate:memory, generate:everything, generate:filesystem, generate:time, generate:git, and others.

2. **Document `mcp-kit probe` exit-1-but-parts-written semantics in the skill.** The open_nodes exit-1 (seq 28) forced the agent to manually inspect the parts directory to confirm success. The skill SKILL.md should state: "probe exit 1 with a written parts file = server returned empty/error payload but shape was captured — read the part file before deciding to retry."

3. **Reduce analyze:memory bash-pipeline churn (~10 unnecessary tool calls).** The analyzer made 28 bash calls, many reformulations of the same python filter that failed due to a parser output format mismatch (int entries in the agents list). The session-analyzer skill should expose a stable extraction API or document the `isinstance(obj, dict)` guard pattern, so analysts don't need to discover this by trial and error.

4. **Preserve the cache-heavy architecture — it is working.** Cache reads are 94%+ of all tokens, keeping per-server cost low (~$2.29 for memory's three agents). Any restructuring of the workflow agent prompts that changes system-prompt content will bust cache keys and increase cost significantly.

5. **Probe more than 3 of 9 tools for complete coverage.** The agent probed only `read_graph`, `search_nodes`, and `open_nodes` (skipping all mutating tools). The 6 un-shaped tools (`create_entities`, `add_observations`, `create_relations`, `delete_entities`, `delete_observations`, `delete_relations`) return `Any`. For a production eval, the skill's "Probe all" default should be honored, with mutating tools marked `⚠ [MUTATING]` and skipped — but non-mutating reads should all be probed.
