---
name: triaging-eval-outputs
description: Use when reviewing generated outputs of the generate-mcp-wrappers eval (per-server session-overview.md / .shapes.json / .py artifacts) to produce owner-split fix reports for mcpgen and eval-kit.
disable-model-invocation: true
---

# Triaging eval outputs

## Overview

Core principle: **mine → attribute by owner → split-emit.** Read all 13 session-overviews in parallel, extract every error with verbatim quotes, confirm code bugs against source `file:line`, then emit two standalone fix reports split by which codebase owns the fix.

## When to use

After a `run-eval` workflow completes and generates `*/session-overview.md` artifacts. For
cross-server pattern detection — not for reviewing a single server in isolation.

## The 6-step workflow

**Step 1 — Inventory**
```bash
ls -la && for d in */; do echo "== $d =="; ls "$d"; done
```
Confirm each server dir has `<server>.py`, `<server>.shapes.json`, `session-overview.md`.
Read `doc/EVAL_REPORT.md` for the verdict matrix.

**Step 2 — Mine (parallel Explore fan-out)**
Dispatch 3 parallel `Explore` agents in one message, each reading ~5 session-overviews.
Prompt each to extract: symptom, server name, **verbatim error quote**, owner tag (`[GEN]` =
mcpgen, `[HARNESS]` = eval-kit), and recurrence count (flag if ≥2 servers).
Do not write reports until mining is complete for all servers.

**Step 3 — Ground**
Read framework source to confirm code bugs with exact `file:line`. Key files:

| Area | File |
|---|---|
| Deterministic verifier (5 checks) | `eval_harness/verify.py` |
| CLI arg surface | `eval_harness/cli.py` |
| Agent prompt template | `agents/server-eval-agent.md` |
| Workflow pipeline | `.claude/workflows/run-eval.js` |
| Server manifest | `servers/servers.toml` |

**Step 4 — Cluster + split**
Group by theme. Tag severity: **P0** = code bug (crash/wrong output), **P1** = recurring
friction ≥2 servers, **P2** = low-priority note. Assign each to one owner (rubric below).
Cross-cutting items appear in both reports with distinct, non-redundant framing.

**Step 5 — Emit two reports**
- `doc/FIXES-mcpgen.md` — generator owner. **Self-contained**: no eval-repo paths,
  verbatim error strings, repro steps.
- `doc/FIXES-eval-kit.md` — harness owner. Every P0 item cites `file:line`. Every item
  names ≥1 affected server.

Use skeletons in `report-templates.md` (this directory).

**Step 6 — Verify**
- Spot-check `file:line` citations against source.
- Every P0 item has a verbatim error string (not paraphrased).
- `doc/FIXES-mcpgen.md` has no eval-repo-only path references.

## Owner boundary rubric

| Component | Owner |
|---|---|
| `mcpgen codegen / list / probe / merge` CLI | **mcpgen** |
| `generate-mcp-wrappers` SKILL.md guidance | **mcpgen** |
| `generate-mcp-runner` SKILL.md guidance (run.py quality) | **mcpgen** |
| `eval-kit verify / report / gen-config` | **eval-kit** |
| `.claude/workflows/run-eval.js` | **eval-kit** |
| `agents/server-eval-agent.md` | **eval-kit** |
| `servers/servers.toml` | **eval-kit** |
| session-analyzer skill | **agent-skills** (flag separately) |

## Common mistakes

- **Grounding skip:** citing `file:line` from memory — always verify against source.
- **Owner blur:** cross-cutting issues need *different* fix framing per owner, not a copy-paste.
- **Paraphrase:** "transport error" ≠ `mcpgen: error: unrecognized arguments: --cmd`. Quote exact.
- **Serial mining:** reading 13 session-overviews one at a time takes 3× longer — fan out.

## Worked example

`doc/FIXES-mcpgen.md` and `doc/FIXES-eval-kit.md` (produced 2026-06-16, 13-server run)
are the canonical reference for what correctly-formed output looks like.
