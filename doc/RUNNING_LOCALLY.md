# Running mcpgen locally

How to use the CLI and the bundled skill directly from a local clone — no `uv tool install`
or PyPI. For server config, auth, and the skill procedure see [USAGE.md](USAGE.md) —
identical once `mcpgen` is on PATH.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- Python ≥ 3.11

---

## Step 1 — Build the kit's venv

Once, from the repo root:

```bash
uv venv                  # creates .venv (Python ≥3.11 auto-selected)
uv pip install -e .      # editable install → .venv/bin/mcpgen
```

Verify:

```bash
ls .venv/bin/mcpgen     # must exist
```

`uv sync` is equivalent and reads `uv.lock` for pinned deps.

---

## Step 2 — Launch Claude with the skill and CLI wired in

From the project you're working in:

```bash
KIT=~/src/mcp-client-kit   # adjust to your clone path

PATH="$KIT/.venv/bin:$PATH" claude --plugin-dir "$KIT"
```

- `PATH="$KIT/.venv/bin:$PATH"` — puts `mcpgen` on PATH for this session. The skill
  shells out to bare `mcpgen`; this is the only setup it needs.
- `--plugin-dir "$KIT"` — loads the bundled `skills/generate-mcp-wrappers/SKILL.md` into
  Claude Code. No copy or symlink needed.

The skill is namespaced as `/mcpgen:generate-mcp-wrappers` (pinned via
`.claude-plugin/plugin.json`). Run `/help` to confirm it's listed.

### Shell alias

```bash
alias claude-kit='PATH="$HOME/src/mcp-client-kit/.venv/bin:$PATH" claude --plugin-dir "$HOME/src/mcp-client-kit"'
```

Or with [direnv](https://direnv.net/), in the project's `.envrc`:

```bash
KIT="$HOME/src/mcp-client-kit"
PATH_add "$KIT/.venv/bin"
```

---

## Troubleshooting (clone-specific)

| Symptom | Fix |
|---------|-----|
| `mcpgen: command not found` | PATH prefix not set or wrong clone path — check `which mcpgen` |
| Skill not listed in `/help` | Wrong `--plugin-dir` — confirm `ls "$KIT/skills/generate-mcp-wrappers/SKILL.md"` |
| `python -m mcpgen` fails | No `__main__.py` in the package; use `python -m mcpgen.cli` |

For auth and config issues see [USAGE.md § Troubleshooting](USAGE.md#troubleshooting).
