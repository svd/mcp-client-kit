---
name: triaging-eval-outputs
description: Use when reviewing generated outputs of the generate-mcp-wrappers eval (per-server session-overview.md / .shapes.json / .py artifacts) to produce owner-split fix reports for mcp-client-kit and eval-kit.
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
mcp-client-kit, `[HARNESS]` = eval-kit), and recurrence count (flag if ≥2 servers).
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

Both filenames carry the run date: `doc/FIXES-<owner>-<YYYY-MM-DD>.md`. Get the date from
`date +%F` — never from memory, and never reuse the date of an existing report. Each run
emits new files; earlier dated reports are left untouched, so the directory accumulates one
pair per triage run.

- `doc/FIXES-mcp-client-kit-<YYYY-MM-DD>.md` — generator owner. **Self-contained**: no
  eval-repo paths, verbatim error strings, repro steps.
- `doc/FIXES-eval-kit-<YYYY-MM-DD>.md` — harness owner. Every P0 item cites `file:line`.
  Every item names ≥1 affected server.

Before writing, `ls doc/FIXES-*.md` and read the most recent prior pair. Items it lists that
still reproduce are re-reported (they are not resolved by having been reported once); items
that no longer reproduce are simply absent — do not carry them forward, and do not add a
"previously reported" status column. The dated files are the history.

Use skeletons in `report-templates.md` (this directory).

**Step 6 — Verify**
- Spot-check `file:line` citations against source.
- Every P0 item has a verbatim error string (not paraphrased).
- `doc/FIXES-mcp-client-kit-<date>.md` has no eval-repo-only path references.
- Both filenames carry today's date and no same-date report was overwritten.

## Owner boundary rubric

| Component | Owner |
|---|---|
| `mcpgen codegen / list / probe / merge` CLI | **mcp-client-kit** |
| `generate-mcp-wrappers` SKILL.md guidance | **mcp-client-kit** |
| `generate-mcp-runner` SKILL.md guidance (run.py quality) | **mcp-client-kit** |
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

The newest `doc/FIXES-mcp-client-kit-<date>.md` / `doc/FIXES-eval-kit-<date>.md` pair is the
canonical reference for what correctly-formed output looks like. As of 2026-08-26 that is the
`2026-08-26` pair (4-server openzeppelin + firecrawl run).
