# mcp-client-kit-eval

Eval framework for the `generate-mcp-wrappers` skill.

## What it is

A framework for running reproducible evaluations of the `mcp-client-kit` `generate-mcp-wrappers` skill. For each MCP server under test, it produces a committed folder containing:

| File | Description |
|------|-------------|
| `<server>.py` | Generated Python wrapper module |
| `<server>.shapes.json` | Shape-spec sidecar (PII-scrubbed) |
| `run.py` | Transport-aware sample runner script |
| `session-overview.md` | Merged narrative explaining how the skill executed |
| `session-analyzer.md` | Raw session-analyzer output |
| `result.json` | Verifier output (feeds the aggregate report) |

## Quick start

```bash
# 1. Create the venv (Python >=3.11 auto-selected)
uv venv

# 2. Install this framework + dev group (generates uv.lock on first run)
uv sync

# 3. Install mcp-client-kit (required — not on PyPI; do this after uv sync)
uv pip install -e ../mcp-client-kit

# 4. Verify
uv run eval-kit --help
```

> **Note:** `mcp-client-kit` is intentionally not listed in `dependencies` because it must be
> installed from a local clone. PyPI does not have it.

## Server manifest

Edit `servers/servers.toml` to add servers under test. See `servers/servers.example.toml` for
the full 15-server example set with comments explaining each field.

## Running an eval (single server)

The framework is driven by a Workflow script (`workflows/run-eval.workflow.js`) that you launch from **Claude Code** (not a standalone script).

Open the workflow in Claude Code and follow the prompts. The workflow runs four stages for each
server: generate, analyze, merge, and verify.

## CLI reference

```bash
eval-kit verify github            # Run 5-check contract on github/
eval-kit report                   # Regenerate doc/EVAL_REPORT.md from all result.json files
eval-kit runner github            # Regenerate run.py for github/
eval-kit merge-session github     # Merge session-overview.draft.md + session-analyzer.md
                                  #   → session-overview.md
```

## Existing eval: github

The `github/` folder contains a completed eval of the GitHub Copilot MCP server (HTTP/Bearer
auth). See `github/session-overview.md` for how the skill executed, and `github/result.json`
for the verification results.

## Architecture

```
servers/servers.toml
        │
workflows/run-eval.workflow.js  (one pipeline per server)
        │
  ┌─────┴──────────────────────────────────────────────┐
  │ 1 Generate  agent runs generate-mcp-wrappers        │
  │ 2 Analyze   session-analyzer on transcript          │
  │ 3 Merge     session-overview.md                     │
  │ 4 Verify    eval-kit verify → result.json           │
  └─────┬──────────────────────────────────────────────┘
        │ (after all servers)
   eval-kit report → doc/EVAL_REPORT.md
```

## Repo layout

```
<server>/               # one folder per evaluated server
  <server>.py
  <server>.shapes.json
  run.py
  session-overview.md
  session-analyzer.md
  result.json
agents/
  server-eval-agent.md  # prompt template for per-server skill agent
doc/
  EVAL_REPORT.md        # generated aggregate matrix
eval_harness/           # Python package (eval-kit CLI)
  manifest.py
  verify.py
  report.py
  runner_gen.py
  runner_templates/
  cli.py
servers/
  servers.toml          # live manifest (user-maintained)
  servers.example.toml  # all 15 servers documented, commented
tests/
workflows/
  run-eval.workflow.js
```

## Git-ignored files

The following are intentionally excluded from version control:

- `*.probe-raw.json` — raw MCP payload dumps (may contain PII)
- `*.shapes.json.parts/` — intermediate per-tool probe part files
- `session-overview.draft.md` — agent self-narrative draft before merging
- `github.bak/` — legacy backup folder

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- `mcp-client-kit` installed from local clone (see Quick start above)
- `pytest>=8` (dev dependency, installed via `uv sync`)
