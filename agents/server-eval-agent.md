# Server eval agent prompt

This file is a **template** for per-server eval runs. The Workflow script fills in the
`{{PLACEHOLDERS}}` before dispatching this prompt to a Claude subagent.

---

## Your task

You are evaluating the `generate-mcp-wrappers` skill for the **{{SERVER_NAME}}** MCP server.

**Server details:**
- Name: `{{SERVER_NAME}}`
- Transport: `{{TRANSPORT}}`
- Launch / URL: `{{LAUNCH}}`
- Auth: `{{AUTH}}`
- Auth notes: {{AUTH_NOTES}}

Your goal is to run the skill end-to-end and produce these artifacts in `eval/{{SERVER_NAME}}/`:

1. `{{SERVER_NAME}}.py` — the generated Python wrapper module
2. `{{SERVER_NAME}}.shapes.json` — shape-spec sidecar (PII-scrubbed)
3. `session-overview.md` — narrative of how the skill executed

At the end, return a structured JSON summary so the Workflow can record the result.

---

## Run the skill

Invoke the **`mcp-client-kit:generate-mcp-wrappers`** skill via the Skill tool to generate the wrappers for `{{SERVER_NAME}}`. Let the skill drive the whole procedure (codegen → list → probe → merge → edit shapes → regenerate → verify).

Use these path conventions so artifacts land where the harness expects:
- module out: `eval/{{SERVER_NAME}}/{{SERVER_NAME}}.py`
- shape-spec: `eval/{{SERVER_NAME}}/{{SERVER_NAME}}.shapes.json`

**All `mcp-kit` commands must write outputs to `eval/{{SERVER_NAME}}/` (relative to the project root) — never to the mcp-client-kit workspace.**

### Quick-start commands

Working directory for ALL commands: {{PROJECT_ROOT}} — **never** run `cd` before any command. You are already in the correct directory.
Manifest: `servers/servers.toml`

**Before any `mcp-kit` command**, ensure the server config is available and set the env var so mcp-kit resolves servers by name:

```bash
uv run eval-kit gen-config           # generates .mcp.eval.json (idempotent, fast)
export MCP_KIT_SERVERS=.mcp.eval.json
```

Use these exact commands for the transport this server uses.
Servers are resolved by name from `MCP_KIT_SERVERS` — do **not** pass `--stdio` or `--url`; that
would bypass name-resolution and lose the manifest `env` block (e.g. `CONTEXT7_API_KEY`).

**stdio (`{{TRANSPORT}}` = stdio):**
```bash
mcp-kit codegen {{SERVER_NAME}} --out eval/{{SERVER_NAME}}/{{SERVER_NAME}}.py
mcp-kit list {{SERVER_NAME}}
mcp-kit probe {{SERVER_NAME}} <tool> --emit-shape eval/{{SERVER_NAME}}/{{SERVER_NAME}}.shapes.json
```

**HTTP no-auth (`{{TRANSPORT}}` = http, `{{AUTH}}` = none):**
```bash
mcp-kit codegen {{SERVER_NAME}} --out eval/{{SERVER_NAME}}/{{SERVER_NAME}}.py
mcp-kit list {{SERVER_NAME}}
```

**HTTP Bearer (`{{TRANSPORT}}` = http, `{{AUTH}}` = bearer:*):**
```bash
mcp-kit codegen {{SERVER_NAME}} --bearer "$<ENV_VAR>" --out eval/{{SERVER_NAME}}/{{SERVER_NAME}}.py
mcp-kit list {{SERVER_NAME}} --bearer "$<ENV_VAR>"
```

`MCP_KIT_SERVERS` is the authoritative config for all mcp-kit subcommands — it applies to `codegen`,
`list`, `probe`, and `call` alike. For stdio servers with an `env` block in the manifest (e.g.
context7), the `${VAR}` references in that block are expanded from the host environment at
parse time, so the key reaches the child process automatically via name-resolution.
`{{LAUNCH}}` is informational (recorded in the manifest); mcp-kit resolves the server by name.

### Non-interactive gate overrides

**You are running as a workflow subagent — you CANNOT call `AskUserQuestion`.** When the skill reaches an interactive gate, do NOT prompt. Apply these defaults instead:

1. **Tool-selection gate (skill Step 2):** probe **all non-mutating** tools; **skip mutating** tools entirely (never call them live). Treat a tool as mutating if its name contains any of: `create`, `update`, `delete`, `remove`, `send`, `set`, `write`, `post`, `patch`, `put`, `cancel`, `approve`, `submit`, `assign`. Record which tools were skipped and why in `session-overview.md`.
2. **Discriminator gate (skill Step 4):** choose the **generic base model** option (or unwrap-only `Any` if no stable shared base exists). Never emit a variant-specific `return_model` from a single-variant probe. A parameter is a discriminator candidate only if its values correspond to **structurally different response shapes** — disqualify immediately if the parameter is a plain filesystem path, a repo identifier, or a pagination hint (e.g. `head`, `tail`, `page`, `limit`, `repoName`, `projectPath`). No need to probe variants for these.
3. **>20-variant cap:** fall back to unwrap-only `Any`.
4. **Sample ids / probe args:** Before probing a tool, check its `inputSchema.required` array. If it is non-empty, do NOT probe with `{}` — call the tool directly with minimal but valid required args on the first attempt and declare those args as `probed_args`. Pass `'{}'` only when `required` is absent or empty. Invent realistic-looking but fake values for required string/ID args.
   - **GitHub:** prefer `owner: "microsoft", repo: "vscode"` as default probe repo. `octocat/Hello-World` has no releases, tags, or issue fields and will produce `[<empty>]` for those tools.
   - **Stateful empty servers (e.g. memory, sqlite):** call a mutating tool once to insert a seed record before probing read-only tools. Record the seed in `session-overview.md` under "Probe setup". This is the ONLY permitted mutating call for seeding purposes.
   - **Quota/auth error responses:** if **every** non-empty tool response is a quota/auth error string (e.g. "Monthly quota exceeded", "Unauthorized"), add `"_probe_status": "inconclusive"` to each affected shape entry. Do NOT leave it as plain `"_observed_shape": "str"` — that looks identical to a genuine text-returning tool.
5. **Run as a single driver thread — do NOT dispatch sub-subagents.**

**PII scrub (mandatory before finishing):** replace all real IDs, emails, usernames, and names in `probed_args` with `<example-*>` placeholders (e.g. `<example-user-id>`, `<example-email>`). Never commit real personal data.

### Step 6: Write session-overview.md

Create `eval/{{SERVER_NAME}}/session-overview.md`. It should be 200–500 words and cover:

- How many tools the server exposes and how many were probed vs. skipped
- Any interesting or surprising responses (unexpected schema, empty results, errors)
- Which mode each probed tool received and a one-line reason
- Any Path-F guard decisions (nested fields omitted)
- Whether the final generated module parsed cleanly

Include a compact mode table:

| Tool | Mode | Reason |
|---|---|---|
| `tool_name` | B | Returns dict with stable scalar fields |

## Artifact format rules

- Folder: `eval/{{SERVER_NAME}}/` — all three artifacts go here, nothing else
- `{{SERVER_NAME}}.shapes.json`: valid JSON, fully PII-scrubbed, `_observed_shape` keys may remain as evidence
- `session-overview.md`: 200–500 words, covers how the skill executed, includes the mode table

---

## Return format

When done, output this exact JSON block (the Workflow parser looks for it):

```json
{
  "server": "{{SERVER_NAME}}",
  "session_id": "<your Claude session ID>",
  "tool_count": 0,
  "shaped_tools": ["tool1", "tool2"],
  "verdict_hint": "pass",
  "notes": "..."
}
```

Do NOT commit or `git add` anything. Leave all four artifacts (`session-overview.md`, `<server>.py`, `<server>.shapes.json`, `result.json`) in the working tree for the user to review and commit manually.

`verdict_hint` values:
- `"pass"` — all probed tools produced honest shapes, generated module parses cleanly
- `"partial"` — some tools were left `Any` that could plausibly be shaped; explain in `notes`
- `"fail"` — generation or probing failed in an unexpected way; explain in `notes`
