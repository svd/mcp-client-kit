# Running mcp-client-kit locally

How to use the CLI and the bundled skill directly from a local clone — no `uv tool install`.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- Python ≥ 3.11

---

## Step 1 — Build the kit's venv

Once, from the repo root:

```bash
uv venv                  # creates .venv (Python ≥3.11 auto-selected)
uv pip install -e .      # editable install → .venv/bin/mcp-kit
```

Verify:

```bash
ls .venv/bin/mcp-kit     # must exist
```

`uv sync` is equivalent and reads `uv.lock` for pinned deps.

---

## Step 2 — Launch Claude with the skill and CLI wired in

From the project you're working in:

```bash
KIT=~/src/mcp-client-kit   # adjust to your clone path

PATH="$KIT/.venv/bin:$PATH" claude --plugin-dir "$KIT"
```

- `PATH="$KIT/.venv/bin:$PATH"` — puts `mcp-kit` on PATH for this session. The skill
  shells out to bare `mcp-kit`; this is the only setup it needs.
- `--plugin-dir "$KIT"` — loads the `skills/generate-mcp-wrappers/SKILL.md` bundled in the
  repo into Claude Code. No copy or symlink needed; the repo already has the required
  `skills/` layout.

The skill is namespaced as `/mcp-client-kit:generate-mcp-wrappers` (pinned via
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

## Step 3 — Configure a server

Config resolves relative to your **current working directory**. Search order (first match wins):

1. `--config <path>` — explicit override
2. `$MCP_KIT_SERVERS` env var — path to a JSON file
3. `~/.mcp-client-kit/servers.json` — user-global fallback
4. `./.mcp.json` — Claude Code format in cwd

Copy the template and edit:

```bash
cp ~/src/mcp-client-kit/servers.example.json .mcp-servers.json
# set MCP_KIT_SERVERS=.mcp-servers.json, or pass --config .mcp-servers.json
```

Both config formats are accepted:

```jsonc
// Simple
{ "myserver": "https://mcp.example.com/mcp/v1" }

// Claude Code format
{ "mcpServers": { "myserver": { "url": "https://mcp.example.com/mcp/v1", "clientName": "my-app" } } }
```

---

## Step 4 — Authenticate

**OAuth:**

```bash
mcp-kit login myserver   # opens browser; token stored at ~/.mcp-client-kit/credentials.json
```

**PAT / Bearer token:**

```bash
export MYSERVER_TOKEN="pat_..."
mcp-kit codegen myserver --bearer "$MYSERVER_TOKEN" --out myserver.py
```

**Local stdio server (no auth):**

```bash
mcp-kit codegen myserver --stdio "python path/to/server.py" --out myserver.py
```

---

## Step 5 — Verify before using the skill

```bash
mcp-kit -h                                         # confirms PATH is correct
mcp-kit codegen myserver --out /tmp/test.py        # confirms config + auth work
```

If this works, all skill steps work.

---

## Step 6 — Run the skill

Invoke `/mcp-client-kit:generate-mcp-wrappers`. The skill runs a 6-step procedure:

| Step | What it does |
|------|-------------|
| 1. Mechanical stubs | `mcp-kit codegen <server> --out <server>.py` — all tools, returns `Any` |
| 2. Curate | Pick tools whose payloads you want typed |
| 3. Probe → skeleton | `mcp-kit probe <server> <tool> --args '...' --emit-shape <server>.shapes.json` |
| 4. Edit shape-spec | Set `unwrap`, `return_model`, `fields`, `input_overrides` — the judgment pass |
| 5. Regenerate | `mcp-kit codegen <server> --out <server>.py --shapes <server>.shapes.json` |
| 6. Verify | `ast.parse` the module; confirm return types |

**Security:** `probe` records live call arguments in `<server>.shapes.json`. Before
committing, scrub `probed_args` — replace real ids/names/PII with placeholders like
`"<example-id>"`. Real values survive deletion via git history.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `mcp-kit: command not found` | PATH prefix not set or wrong clone path — check `which mcp-kit` |
| Skill not listed in `/help` | Wrong `--plugin-dir` — check `ls "$KIT/skills/generate-mcp-wrappers/SKILL.md"` |
| `python -m mcp_client_kit` fails | No `__main__.py` in the package; use `python -m mcp_client_kit.cli` |
| `ReauthenticationRequired` | Run `mcp-kit login <server>` |
| Config not found | Check search order above; paths resolve from cwd |
