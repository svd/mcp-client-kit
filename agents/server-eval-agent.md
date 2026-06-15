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

Your goal is to run the skill end-to-end and produce these committed artifacts in `{{SERVER_NAME}}/`:

1. `{{SERVER_NAME}}.py` — the generated Python wrapper module
2. `{{SERVER_NAME}}.shapes.json` — shape-spec sidecar (PII-scrubbed)
3. `run.py` — sample runner script (transport-aware)
4. `session-overview.draft.md` — narrative of how the skill executed

At the end, return a structured JSON summary so the Workflow can record the result.

---

## Step-by-step instructions

### Step 1: Generate mechanical stubs

Run the codegen command to produce a Python wrapper with one `async def` per tool, all returning `Any`:

```bash
mcp-kit codegen {{SERVER_NAME}} --out {{SERVER_NAME}}/{{SERVER_NAME}}.py
```

Inspect the output. You will see one function per tool, signature derived from the tool's input schema, body is a single `mcp_call(...)` returning `Any`. No live calls are made here — this is purely schema-driven. Note the total number of tools generated; you will report this in the JSON summary.

### Step 2: List and classify tools

Print the full tool inventory:

```bash
mcp-kit list {{SERVER_NAME}}
```

Read through the tool names. Mark any tool as **mutating** if its name contains any of these substrings: `create`, `update`, `delete`, `remove`, `send`, `write`, `post`, `patch`, `put`, `cancel`, `approve`, `submit`, `assign`.

In this eval context, **probe all non-mutating tools**. Skip mutating tools entirely — do not call them live. Note which tools were skipped and why in `session-overview.draft.md`.

### Step 3: Probe non-mutating tools

For each non-mutating tool, call it live and record the observed shape:

```bash
mcp-kit probe {{SERVER_NAME}} <tool_name> --args '<json_args>' --emit-shape {{SERVER_NAME}}/{{SERVER_NAME}}.shapes.json
```

Use minimal, safe arguments. If a tool takes no required arguments, pass `'{}'`. If it requires an ID or query string, use a realistic but generic value.

**PII warning:** Before committing, replace all real IDs, emails, usernames, and names in `probed_args` with `<example-*>` placeholders (e.g., `<example-user-id>`, `<example-email>`). Never commit real personal data.

### Step 3b: Merge shape parts

After probing all tools, merge the per-tool shape fragments into a single sidecar:

```bash
mcp-kit merge {{SERVER_NAME}} --out {{SERVER_NAME}}/{{SERVER_NAME}}.shapes.json
```

Open `{{SERVER_NAME}}.shapes.json` and verify every probed tool has an entry with at least `_observed_shape` recorded.

### Step 4: Edit shapes.json — the judgment step

This is the most important step. For each entry in `{{SERVER_NAME}}.shapes.json`, decide which mode applies and edit the JSON accordingly.

**Mode A — Plain text / string response**
The tool returned a string or unstructured text. Leave `return_model: null`. The wrapper stays `-> Any`. Document why in the session overview.

```json
{ "return_model": null }
```

**Mode B — JSON dict response (single object)**
The tool returned a predictable dictionary. Set `return_model` to a PascalCase name, list the top-level scalar fields under `fields`, and set `unwrap` to the key path if the payload is nested inside a wrapper key.

```json
{
  "return_model": "UserProfile",
  "fields": { "id": "str", "name": "str", "email": "str", "created_at": "str" },
  "unwrap": "data"
}
```

**Mode C — JSON list response**
The tool returned a list of similar dicts. Set `return_container: "list"`, then fill `return_model` and `fields` the same as Mode B.

```json
{
  "return_container": "list",
  "return_model": "SearchResult",
  "fields": { "id": "str", "title": "str", "score": "float" },
  "unwrap": "results"
}
```

**Path-F guard — omit nested non-scalars from `fields`**
Only include top-level stable scalar fields (`str`, `int`, `float`, `bool`) in `fields`. Omit any field whose value is itself a dict or a list-of-objects. Nested structures are unstable across API versions and must not be typed. If a tool's response is *entirely* nested non-scalars with no stable top-level scalars, fall back to Mode A.

**Decision matrix:**

| Response shape | `return_container` | `return_model` | `fields` | Mode |
|---|---|---|---|---|
| Plain string / text | — | `null` | — | A |
| Dict with scalar top-level keys | — | `"ModelName"` | top-level scalars only | B |
| List of similar dicts | `"list"` | `"ModelName"` | top-level scalars only | C |
| Dict whose values are all nested objects | — | `null` | — | A (Path-F) |

### Step 5: Regenerate

With the edited shapes file in place, regenerate the wrapper to apply the type annotations:

```bash
mcp-kit codegen {{SERVER_NAME}} --out {{SERVER_NAME}}/{{SERVER_NAME}}.py --shapes {{SERVER_NAME}}/{{SERVER_NAME}}.shapes.json
```

After generation, verify the output parses cleanly:

```bash
python -c "import ast; ast.parse(open('{{SERVER_NAME}}/{{SERVER_NAME}}.py').read()); print('OK')"
```

If `ast.parse` raises a `SyntaxError`, fix the shapes entry that caused it and regenerate. Do not hand-edit the generated `.py` file.

### Step 6: Write session-overview.draft.md

Create `{{SERVER_NAME}}/session-overview.draft.md`. It should be 200–500 words and cover:

- How many tools the server exposes and how many were probed vs. skipped
- Any interesting or surprising responses (unexpected schema, empty results, errors)
- Which mode each probed tool received and a one-line reason
- Any Path-F guard decisions (nested fields omitted)
- Whether the final generated module parsed cleanly

Include a compact mode table:

| Tool | Mode | Reason |
|---|---|---|
| `tool_name` | B | Returns dict with stable scalar fields |

### Step 7: Generate run.py

Run the eval-kit runner to produce a transport-aware sample script:

```bash
eval-kit runner {{SERVER_NAME}}
```

This writes `{{SERVER_NAME}}/run.py`. Do not hand-edit it.

---

## Artifact format rules

- Folder: `{{SERVER_NAME}}/` — all four artifacts go here, nothing else
- `{{SERVER_NAME}}.shapes.json`: valid JSON, fully PII-scrubbed, `_observed_shape` keys may remain as evidence
- `session-overview.draft.md`: 200–500 words, covers all 6 steps, includes the mode table
- `run.py`: generated by `eval-kit runner` — do not hand-edit

---

## Return format

When done, output this exact JSON block (the Workflow parser looks for it):

```json
{
  "server": "{{SERVER_NAME}}",
  "tool_count": 0,
  "shaped_tools": ["tool1", "tool2"],
  "modes_hit": ["A", "B"],
  "verdict_hint": "pass",
  "notes": "..."
}
```

`verdict_hint` values:
- `"pass"` — all probed tools produced honest shapes, generated module parses cleanly
- `"partial"` — some tools were left `Any` that could plausibly be shaped; explain in `notes`
- `"fail"` — generation or probing failed in an unexpected way; explain in `notes`
