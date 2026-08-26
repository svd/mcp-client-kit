# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Eval framework for the `generate-mcp-wrappers` skill from `mcp-client-kit`. Runs reproducible evals of that skill against real MCP servers, collecting generated wrappers, shape specs, session analyses, and verification results.

## Setup

```bash
uv venv && uv sync
```

`mcp-client-kit` is a normal dependency resolved through `[tool.uv.sources]`. It currently
points at the local dev checkout `../mcp-client-kit` (editable), so the engine follows
whatever branch is checked out there.

## Commands

```bash
# Run tests
uv run pytest

# Verify a single server's generated artifacts
uv run eval-kit verify <server>

# Regenerate doc/EVAL_REPORT.md
uv run eval-kit report
uv run eval-kit report --with-narrative

# Regenerate .mcp.eval.json from servers.toml
uv run eval-kit gen-config
```

## Running evals

Evals are driven by a named workflow — use slash commands inside Claude Code, not free text:

```
/run-eval time               # single server
/run-eval time memory        # space-separated
/run-eval all                # every server in servers.toml
```

After a multi-server run:

```
/triaging-eval-outputs
```

To move the eval to a different `mcp-client-kit` release, run this first — it pins the
engine, checks out the matching skill worktree, and gates the run until both agree:

```
/rerun-eval-at-version 0.7.0     # or: latest
```

## Architecture

```
servers/servers.toml          # live server manifest (user-maintained)
.mcp.eval.json                # generated MCP config (eval-kit gen-config)
eval_harness/
  manifest.py                 # ServerSpec dataclass + servers.toml loader
  verify.py                   # 5-check verifier (ast, signatures, idempotency, pii, roundtrip)
  report.py                   # aggregate EVAL_REPORT.md generator
  versions.py                 # engine + skill version detection
  gen_config.py               # .mcp.eval.json generator
  cli.py                      # eval-kit CLI entry point
.claude/workflows/run-eval.js # /run-eval workflow (per-server pipeline: generate → verify → analyze → synthesize)
.claude/skills/               # /triaging-eval-outputs, /rerun-eval-at-version skills
agents/server-eval-agent.md   # prompt template used by the per-server agent inside run-eval.js
```

**Per-server pipeline** (run-eval.js): generate wrapper → eval-kit verify + runner → session-analyzer → narrative synthesis → eval-kit report. Verify runs before analyze so the analyzer reads `result.json` and `run.py` as ground truth instead of predicting them.

**verify.py checks** (in order): `ast` (syntax), `signatures` (return types vs shapes.json), `idempotency` (render_module() deterministic; renders from the real `<server>.mcpgen.json` schemas when present, stubs otherwise — `result.json` records which), `pii` (no PII in shapes.json probed_args), `roundtrip` (live call returns typed dict). Roundtrip skip reasons are self-describing, and `report.py` renders a true N/A differently from a coverage gap. N/A: `no_shaped_tool_by_design` (every tool returns prose — expected), `only_mutating_shaped_tools`, `oauth_not_supported_in_verifier`, `missing_cred_<VAR>`, `probed_args_contain_placeholders`. Gaps: `shapes_json_empty`, `probe_inconclusive` (no probe returned a success payload, so nothing was established — the marker carries no cause, so neither `verify.py` nor the report may name one).

## Server manifest

`servers/servers.toml` defines which servers to eval. Fields: `name`, `transport` (`stdio`/`http`/`sse`), `launch` (command or URL), `auth` (`none`/`oauth`/`bearer:ENV_VAR`), `expected_modes`, `notes`, `env`, `seed`.
`seed` is a list of shell commands run before probing, for servers whose read tools
return nothing until the store is written to (the eval subagent skips all mutating tools). See `servers/servers.example.toml` for all 15 documented examples.

## Output layout

Each eval run writes to `eval/<server>/`:

| File | Description |
|------|-------------|
| `<server>.py` | Generated wrapper module |
| `<server>.shapes.json` | Shape-spec sidecar (PII-scrubbed) |
| `run.py` | Transport-aware sample runner |
| `session-overview.md` | Merged narrative of how the skill executed |
| `result.json` | Verifier output |
| `session-analyzer.md` | Raw session-analyzer output (local only) |
| `<server>.verify.json` | Unscrubbed probe args for roundtrip (local only) |

`eval*/` is gitignored, so tracked server folders are force-added a subdirectory at a
time after review. Five files per folder are committed: `<server>.py`,
`<server>.shapes.json`, `run.py`, `session-overview.md`, `result.json`. The rest stay
local — `session-analyzer.md` is superseded by `session-overview.md`, and
`<server>.verify.json` holds unscrubbed, machine-specific probe args. `doc/EVAL_REPORT.md`
is the committed aggregate output.

## Key invariants

- `mcp-client-kit` in pyproject.toml is the version under test, in one of two modes: local dev (`[tool.uv.sources]` editable path to `../mcp-client-kit` — current) or a pinned release (`==<X>` plus a plugin worktree at tag `v<X>`). Switch to a release through `/rerun-eval-at-version`, never by hand, so the plugin moves with the engine.
- The plugin marketplace directory and the installed engine must point at the same checkout. A dev engine against a release skill (or the reverse) produces results attributable to neither.
- Every `result.json` carries a `versions` stamp (`engine`, `skill_ref`, `skill_path`) from `eval_harness/versions.py`; `report.py` renders it in the report header and flags mixed runs. `skill_path` is home-contracted to `~/...` — `result.json` is committed and must not carry a username.
- `${VAR}` placeholders in `.mcp.eval.json` stay literal (not expanded at gen time).
- `*.probe-raw.json` files may contain PII — gitignored, never commit.
- `*.verify.json` holds the unscrubbed twin of `shapes.json` `probed_args`, with real local paths. Gitignored; `verify.py` reads it opportunistically so roundtrip uses args the server accepts instead of placeholders. Absent (a fresh clone), roundtrip degrades to a `probed_args_contain_placeholders` skip — expected, not a regression. Entries holding server-side ephemeral ids (job ids, crawl ids) go stale on their own schedule; once they do, roundtrip falls through to a different candidate tool. A roundtrip that exercises a different tool between runs is expected, not a regression.
- Each committed `eval/<server>/` folder is the faithful record of exactly one skill run. Hand edits made after the run to `<server>.shapes.json`, `<server>.py`, `run.py`, or `result.json` are reverted before commit — a hand-resolved shape measures the human, not the skill, and `report.py` aggregates it as skill output. Findings from post-hoc probing go into `doc/FIXES-*.md` (and real ids into the gitignored `<server>.verify.json`), never back into the committed artifacts. To improve a server's artifacts, rerun the eval.
- `run.py` is generated by `mcp-client-kit:generate-mcp-runner`, not by eval-kit itself.
