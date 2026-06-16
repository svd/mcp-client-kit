---
name: generate-mcp-runner
description: Use when generating a runnable, shape-aware smoke-test `run.py` for an MCP server's generated wrappers. Reads existing artifacts produced by `generate-mcp-wrappers` (wrapper module, shapes.json, verify.json), selects read-only tools, chooses real args from verify.json, emits one call per probed discriminator variant, and validates the output statically. Accepts free-text extra instructions (filter tools, force args, add auto-run, etc.).
---

# Generate MCP runner (`run.py`)

`generate-mcp-wrappers` produces a typed wrapper module plus a `shapes.json` sidecar
(and a gitignored `verify.json` with pre-scrub real args). **This skill is the next step:**
it authors a `run.py` that exercises those wrappers in a sensible, shape-aware workflow —
one that a template cannot produce because it needs semantic understanding of the tools,
their return shapes, and the server's domain.

The output is a standalone async script: connection boilerplate (from a proven skeleton),
then one block per read-only tool — real args, discriminated variants called once each,
and print statements that surface something meaningful from each response shape.

**Invoke as:** "generate runner for radar", "generate runner for github", "runner for time".

## What this skill consumes

Artifacts already produced by `generate-mcp-wrappers` for `<server>` output dir `<server>/`:

| Artifact | Path | Purpose |
|---|---|---|
| Wrapper module | `<server>/<server>.py` | function names, signatures (`async def <tool>(caller, *, <kwargs>) -> <Model\|Any>`), return annotations, module-level `SERVER` |
| Shape-spec | `<server>/<server>.shapes.json` | `return_model`, `return_container`, `discriminator`+`variants`, `fields`, `probed_args` (scrubbed) |
| Verify sidecar | `<server>/<server>.verify.json` | **gold source**: real pre-scrub `probed_args`, gitignored, present locally |
| Server descriptions | `mcp-kit list <server>` | tool purpose, workflow ordering |

## Connection skeleton selection

Select the skeleton from `runner_templates/` based on how the wrappers were generated:

| transport | auth | Template | Connection setup |
|---|---|---|---|
| `stdio` | none | `stdio.py` | `McpBridgeCaller(cmd="<launch>")` |
| `http`/`sse` | none | `http_public.py` | `McpBridgeCaller(url=SERVER_URL)` |
| `http`/`sse` | bearer | `http_bearer.py` | read `os.environ["<ENV_VAR>"]` + `McpBridgeCaller(url=SERVER_URL, bearer=bearer)` |
| `http`/`sse` | oauth | `http_oauth.py` | `await ensure_login(SERVER_NAME)` + `McpBridgeCaller(url=SERVER_URL)` |

**Alternative OAuth pattern (registry-configured servers):** When the server is registered
in `~/.mcp-client-kit/servers.json` (i.e. the original codegen used `--config`), prefer the
simpler form used in hand-authored runners:

```python
from pathlib import Path
CONFIG_PATH = Path.home() / ".mcp-client-kit" / "servers.json"
caller = McpBridgeCaller(config_path=CONFIG_PATH)
```

No `ensure_login` call is needed — `McpBridgeCaller` resolves auth from `servers.json` and
the credentials cache. Use this pattern when you can confirm the server appears in that file.

The `(transport, auth_kind)` mapping (mirrors `runner_gen.py:_TEMPLATE_KEYS`):

```python
("stdio",  "none")   → stdio
("http",   "none")   → http_public
("sse",    "none")   → http_public
("http",   "bearer") → http_bearer
("sse",    "bearer") → http_bearer
("http",   "oauth")  → http_oauth
("sse",    "oauth")  → http_oauth
("stdio",  "oauth")  → stdio          # unusual; stdio needs no login
("stdio",  "bearer") → stdio          # bearer carried in env or launch cmd
```

Read the skeleton verbatim and keep its header unchanged. **Only author the `$demo_calls`
block** — replace it with the actual tool call sequence you derive in steps 4–6 below.

## Procedure

### 1. Locate artifacts

Find for `<server>`:
- `<server>/<server>.py` — if **missing**, stop and tell the user to run
  `generate-mcp-wrappers` first. Do not continue.
- `<server>/<server>.shapes.json` — always required.
- `<server>/<server>.verify.json` — preferred; **gitignored, present locally**. If missing:
  fall back to scrubbed `shapes.json.probed_args`, then to schema-minimal args derived
  from the wrapper signature. Note the degradation in a comment inside `run.py`.

Also determine transport and auth kind from context (how the wrappers were generated, any
`servers.json` entry, or the user's original `codegen` invocation).

### 2. Enumerate tools

Read every `async def` signature from `<server>/<server>.py`. For each tool collect:
- function name (as called: `module.<fn>(caller, **kwargs)`)
- parameter names, types, and defaults
- return annotation (`-> Model`, `-> list[Model]`, `-> Any`)

Cross-check tool descriptions by running:
```bash
mcp-kit list <server>
```
This gives the human-readable description for each tool — you need these to decide workflow
ordering and to identify which calls make sense together.

### 3. Classify read-only vs mutating

Flag tools as **mutating** when the tool name or description contains any of:

```
create, update, delete, remove, send, set, write, post, patch, put,
cancel, approve, submit, assign
```

**Default: emit only read-only tools.** Mutating tools are skipped by default. List the
skipped tools in a `# Skipped mutating tools: ...` comment at the top of `main()`. They
are opt-in via explicit extra instruction (e.g. "include the export tools", "add create_entity").

### 4. Choose args per tool

For each tool, pick args in priority order:

**(a) Real args from `verify.json`** *(preferred)*  
The `verify.json` is a flat `{tool_name: probed_args}` map. The value is either:
- `{"key": value, ...}` — single probe → use directly as kwargs
- `[{...}, {...}, ...]` — multi-probe → use the first entry for a plain call; for
  **discriminated** tools (see below) use the per-variant entries

**(b) Scrubbed `shapes.json.probed_args`** *(fallback)*  
Same structure; may contain `<example-*>` placeholders where PII was scrubbed. Placeholders
are non-functional — note in a comment that real values are needed. If `probe_args_scrubbed`
is `true` in the shape entry, note that the user must supply real args.

**(c) Schema-minimal args** *(last resort)*  
Derive from the wrapper signature: required params get representative minimal values
(`entityType=1`, `query={"operator":"AND","conditions":[]}`, `size=1`, string params get
`"<value>"`). Non-required params are omitted. Clearly comment that these are synthetic.

**Discriminated tools** (shape entry has `discriminator` + `variants`):  
Emit **one call per probed variant**. The `verify.json` list for that tool contains one
arg-dict per probed variant in order. Match each list entry to its variant by the
discriminator field value (e.g. `entityType=1` → variant `"1"`, etc.). If the list has
fewer entries than variants, emit the available ones and comment that remaining variants
were not probed.

### 5. Order calls into a workflow

Use tool descriptions from `mcp-kit list` to establish a sensible execution order. General
pattern:

```
1. Identity / whoami / current user
2. Metadata / glossary / field catalog
3. Filter discovery (get_filters, list_*, etc.)
4. Detail queries that depend on filter values (get_filter_values, etc.)
5. Main search / query / list calls (one per discriminated variant, clustered together)
6. Aggregation / grouping
7. Column / field schema tools
8. Saved state tools (saved searches, favorites, etc.)
```

Cluster discriminated-tool variants together (all `query_radar(entityType=1/2/3)` blocks
adjacent). Let the server's semantic context override the generic order — a tool description
often tells you exactly what it depends on.

### 6. Emit `<server>/run.py`

Write the file with this structure:

**a. Header:** chosen connection skeleton verbatim, with placeholders filled:
- `$server_name` → `<server>` (the server name string)
- `$module_name` → `<server>` with `-` replaced by `_`
- `$launch` → the actual stdio command or HTTP URL
- `${bearer_env_var}` → the env var name (e.g. `GITHUB_TOKEN`)

**b. Call body** (replaces `$demo_calls`): for each tool in workflow order, emit one block:

```python
    # <tool_name> -> <ReturnAnnotation>
    result = await <module>.<fn>(caller, param=value, ...)
    <shape-aware print>
```

**Shape-aware print conventions** — choose based on `shapes.json` entry:

| Shape | Print |
|---|---|
| `return_container == "list"` | `print(f"<tool>: {len(result)} item(s)")` |
| dict with known `fields` | Drill into 1–2 top-level scalar fields: `print(f"<tool>: id={result.get('id')!r}  name={result.get('name')!r}")` |
| nested dict, `total` / `count` present | Include pagination info: `print(f"... total={result.get('total')}")` |
| `-> Any` / scalar | `print(f"<tool>: {type(result).__name__}")` |

Use a distinct variable name per call (`me`, `glossary`, `filters`, `fv`, `persons`, etc.)
rather than reusing `result` — it makes the script readable as a flow.

**c. Discriminated tool variants** — emit adjacent blocks:

```python
    # <tool> -> list[Variant1]  (discriminator=1)
    v1 = await <module>.<fn>(caller, discriminator=1, ...)
    print(f"<tool>(1): {len(v1)} record(s)")

    # <tool> -> list[Variant2]  (discriminator=2)
    v2 = await <module>.<fn>(caller, discriminator=2, ...)
    print(f"<tool>(2): {len(v2)} record(s)")
```

**d. Skipped tools comment** — before the first call in `main()`:

```python
    # Skipped mutating tools: create_entity, update_entity, delete_entity
```

**e. Docstring** — the file-level `"""..."""` from the chosen skeleton already documents
the transport and usage. Keep it; update the URL/cmd if the skeleton uses a placeholder.

### 7. Validate statically

Run both checks on the emitted file:

```bash
python -c "import ast; ast.parse(open('<server>/run.py').read())"
python -m py_compile <server>/run.py
```

Report: **PASS** if both succeed. **FAIL** + quoted error if either fails — fix the
syntax error and re-validate before returning.

**Do not auto-run** `run.py` against the live server. The generated file's docstring
documents how to run it manually. Auto-run may be added via explicit extra instruction.

## Defaults (apply unless overridden by extra instructions)

- **Tool scope**: read-only tools only.
- **Call count**: one call per tool; one call per probed variant for discriminated tools.
- **Args source**: verify.json → shapes.json.probed_args → schema-minimal.
- **Output path**: `<server>/run.py`.
- **Validation**: static (`ast.parse` + `py_compile`) only; no live run.

## Extra instructions

Accept free-text extra instructions that modify any default. Weave them into the selection,
args, and ordering — do not ask for permission to apply them. Examples:

| Instruction | Effect |
|---|---|
| "only `query_radar` and `global_search`" | emit exactly those two tools |
| "include the export tools" | also emit flagged mutating tools matching "export" |
| "use entityType 2 and 7" | override discriminated variant set to those values |
| "skip whoami" | omit that tool from the runner |
| "add an auto-run section at the end" | append `asyncio.run(main())` inline run comment + instructions |
| "use `size=5` for all queries" | override `size` arg across all calls |

## Guards

- **Never run `run.py`** automatically. Validation is static only.
- **Never author calls to mutating tools** unless explicitly instructed. Skip by default
  and log the skipped names in a comment.
- **Prefer `verify.json` args** over schema-minimal synthesized args. Real args produce
  meaningful smoke results; synthetic args may fail against the live server.
- **One call per discriminated variant**, not one generic call. A single `query_radar(entityType=1)`
  only tests one return shape — the whole point of discriminated tools is that the response
  type changes per variant.
- **Keep the connection skeleton header verbatim** — it encodes proven auth/transport wiring.
  Only replace the placeholder block (`$demo_calls` region and the `$`-variables).
- **`run.py` must import the generated module**, not `mcp_client_kit` directly for tool calls.
  The `await module.<fn>(caller, ...)` form is the only supported call site; never call
  `caller.call(SERVER, "raw-tool-name", {...})` from `run.py`.
