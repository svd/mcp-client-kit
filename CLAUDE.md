# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup (once)
uv venv && uv pip install -e .

# Tests
uv run pytest                          # all tests
uv run pytest tests/test_codegen.py    # single file
uv run pytest -k "test_name"           # single test

# Lint / type-check
uv run ruff check mcpgen/
uv run mypy

# Build dist
uv build

# Run CLI from clone (no install)
.venv/bin/mcpgen codegen <server> --out <server>.py
```

## Architecture

Four layers, strict one-way dependency:

```
CLI (cli.py)
  └── Bridge (_bridge.py)     ← auth, transport, live MCP sessions
  └── Codegen (codegen.py)    ← pure functions: JSON Schema → Python source
  └── Discovery (discovery.py)← pure enumeration of host-configured servers
Generated wrappers (.py files)
  └── Seam (seam.py)          ← McpCaller Protocol only; no concrete client
```

**`seam.py`** — the only thing generated wrappers import from this package. A single `McpCaller` Protocol (`async def call(server, tool, arguments) → Any`). Changing it breaks all generated files.

**`codegen.py`** — pure functions (no I/O). `render_module()` takes tool dicts + optional shape-spec and emits Python source. `summarize_shape()` converts a live response to a shape skeleton. `merge_skeletons()` merges multiple probe skeletons into one.

**`_bridge.py`** — all async/network code. `McpBridgeCaller` implements `McpCaller`. `session()` is the `asynccontextmanager` for raw MCP sessions. Contains OAuth pre-flight refresh logic (critical — see module docstring; bounded to `mcp<2` because SDK `_initialize` never calls `update_token_expiry`).

**`discovery.py`** — sync, no network. Reads Claude Code CLI / `~/.claude.json` to enumerate configured MCP servers.

**`cli.py`** — thin argparse wiring over the above. Each `_cmd_*` function maps to a subcommand.

## Shape-spec sidecar

`<server>.shapes.json` is the hand-editable file that drives typed codegen. Key fields per tool entry: `unwrap` (key path to unwrap), `return_model` (TypedDict name), `fields` (field → Python type), `input_overrides`, `overloads`. The `.parts/` directory holds per-probe intermediates until `mcpgen merge` consolidates them.

## Running the skill locally

```bash
KIT=~/src/mcp-client-kit
PATH="$KIT/.venv/bin:$PATH" claude --plugin-dir "$KIT"
```

The skill is `/mcp-client-kit:generate-mcp-wrappers`. It requires `mcpgen` on PATH.

## Server config

`servers.example.json` → copy to `servers.json` (or set `$MCPGEN_SERVERS`). Config is `{"mcpServers": {"name": {"type": "http", "url": "..."}}}` — same shape as Claude Code's MCP config.

## Credentials

Stored at `~/.mcpgen/credentials.json` (chmod 0600) or OS keychain via `--cred-backend keyring`. `migrate-creds` moves between backends.

## Versioning & release

Two artifacts, independent versions — see `doc/DISTRIBUTION.md` for full spec.

| Artifact | Tag form | Source of truth | Effect |
|---|---|---|---|
| engine (CLI) | `vX.Y.Z` | `pyproject.toml` | triggers PyPI publish |
| plugin (skill) | `plugin-vX.Y.Z` | `.claude-plugin/plugin.json` + `marketplace.json` | marketplace ref only, no publish |

```bash
# Plugin-only release (skill/SKILL.md change, no engine code change)
git tag plugin-vX.Y.Z origin/main   # tag on main after PR merge
git push origin plugin-vX.Y.Z

# Engine release — see doc/DISTRIBUTION.md:108 (fires PyPI publish workflow)
```
