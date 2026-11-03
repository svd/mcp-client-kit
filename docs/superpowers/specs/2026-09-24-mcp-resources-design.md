# MCP resources support in mcpgen

## Context

mcpgen handles tools only (`list_tools` / `call_tool`). Real servers put reference data the tools depend on into **resources**. For example, `delivery_central` exposes `risk://dictionary` ("read this before calling" `upsert_unit_risk`), `dimension://all`, and the template `dimension://by-unit-type/{unitType}`. Generated wrappers can't reach any of them today.

Goal: first-class resources across the whole pipeline: CLI inspection, generated accessors, typed returns via probe/shapes, and drift detection. Only additive changes: existing generated modules, `shapes.json` files, `.mcpgen.json` manifests and custom `McpCaller` implementations keep working unchanged.

## Settled decisions

| Decision | Choice |
|---|---|
| Scope | CLI inspect + codegen accessors + typed returns (probe) + drift detection |
| Seam | New `McpResourceReader` Protocol (`read_resource(server, uri) -> Any`); `McpCaller` untouched; `McpBridgeCaller` implements both |
| Runtime decoding | text → same json → literal_eval → str parse as tools; blob → base64-decoded `bytes`; several contents → list; empty → `ValueError` |
| Accessor naming | Derived from the **URI**, not `name`: `risk://dictionary` → `read_risk_dictionary`; template expressions dropped: `dimension://by-unit-type/{unitType}` → `read_dimension_by_unit_type`. Reason: `name` is non-unique and often a Python function name (FastMCP), while the URI is already the key everywhere else. |
| Keying | Resource entries in shapes.json, parts, verify.json and manifest are keyed by the normalized URI or the uriTemplate string. `:` can't appear in tool names, so keys don't collide. Resource shape entries carry `"kind": "resource" \| "resource_template"`. |
| Manifest | Stays `format_version: 1`. Two new **optional** sections, `resources` and `resource_templates`. Absent = untracked; present (even `{}`) = tracked. Older mcpgen versions ignore the extra keys, so no hard failure in pinned CI. |
| Drift | Added/removed resource or template, or a `mimeType` change → drift (exit 1). `name`/`description` change → advisory. Stored manifest without the sections + live resources present → advisory ("run `check --update`"), exit 0. |
| Codegen flag | `--resources {all,templates,none}`, default `all`. Stderr warning above 50 static resources (servers that list their data as resources). |
| URI templates | RFC 6570 levels 1–4 for scalar values. Variables under `?`/`&` are optional (`T \| None = None`); all others required. A malformed template → warning on stderr, accessor skipped. |

## Design by layer

**`mcpgen/seam.py`**
- Add a `@runtime_checkable class McpResourceReader(Protocol)`.
- Update the docstring from "single Protocol" to "these Protocols".
- Export it from `mcpgen/__init__.py`.

**`mcpgen/_bridge.py`**
- Extract `_parse_text(text)` from `_parse_one` (~2156). `_parse_one` behaviour stays identical.
- `parse_resource(contents)` implements the decoding row of the table.
- `normalize_resource_uri(uri)` uses a pydantic `AnyUrl` TypeAdapter. The SDK normalizes URIs (`Dimension://ALL` → `dimension://ALL`, spaces → `%20`), so every key must pass through the same normalization.
- `McpBridgeCaller.read_resource(server, uri)` mirrors `call()` (~2107): one-shot session, or `self._block.session_for(server)` inside `connected()`. The static `_read(s, uri)` calls `s.read_resource(AnyUrl(uri))` and passes the result to `parse_resource`.
- `list_resource_inventory(s) -> (resources, templates)`:
  - Gated on `s.get_server_capabilities().resources`: no capability → `([], [])` and no requests sent.
  - Paginates with `params=PaginatedRequestParams(cursor=…)`. Guards against a repeated cursor and caps at 1000 pages.
  - `McpError` METHOD_NOT_FOUND on the templates list → empty.

**`mcpgen/codegen.py`** (stays pure)
- URI templates: `UriVar`, `parse_uri_template`, `uri_template_variables -> [(raw_name, required)]`, `expand_uri_template`. Also an emitted `_EXPAND_URI` helper source, in the style of `_DIG`, with a local `from urllib.parse import quote`.
- `resource_function_names(tools, resources, templates)` derives names from URIs. It de-duplicates against sanitized tool names and module-level names (`SERVER`, `_dig`, `_dig_list`, `_expand_uri`) with a `_2` suffix.
- `render_resource(...)` / `render_resource_template(...)`:
  - First parameter `caller: McpResourceReader`; template variables as keyword-only `str` (or `input_overrides[raw]`). A variable that sanitizes to `caller` becomes `caller_`.
  - The values dict is built inline, and raw variable names are kept for expansion.
  - Docstring: description, then a stable `Resource: <uri>` / `Resource template: <tpl>` line, then the mimeType, plus `_with_size_note`.
  - `unwrap` / `return_model` / `return_container` / `fields` behave exactly as in `render_tool` (~566–577). `discriminator`/`variants` are ignored with a warning.
- `render_module(..., resources=None, resource_templates=None)`:
  - Output is **byte-identical** when there are no resources.
  - Fix: `needs_json`, `_DIG`/`_DIG_LIST` gating and model registration currently look at tools only; they must include rendered resources too.
  - Header imports `McpResourceReader` only when resources are rendered.
  - Accessors come after the tools.
- `probe_skeleton(..., kind=None)` adds `"kind"` only when given, so tool parts stay byte-stable.

**`mcpgen/manifest.py`**
- `build(server, tools, resources=None, resource_templates=None)`:
  - Omits the sections when resources weren't collected.
  - Entries hold `{name, description, mimeType}`.
- `_validate` checks that a section, when present, is a dict.
- `DriftReport` gains defaulted `resources_added/removed/changed/advisory/untracked` and `resources_compared`; `has_drift` includes the resource drift categories.
- `render_text` gets `ADDED server resource <uri>` lines.
- `to_json` adds a `"resources"` block only when resources were compared or are untracked. This keeps the exact-JSON assertions in `test_cli_check.py:264` and `test_manifest.py:283` passing.

**`mcpgen/cli.py`**
- **Inventory without test churn:**
  - `_list_tools(..., on_session=None)` awaits the hook inside the same session.
  - New `Inventory` dataclass and `_list_inventory(server, *, scope, **conn)`, which calls the module-global `_list_tools` with a hook that collects resources: one session, one OAuth handshake.
  - The 51 existing `patch("mcpgen.cli._list_tools")` sites stay valid: a patched `_list_tools` never runs the hook, so the result is `resources=None`, which means untracked and gives unchanged output.
  - New flags are read via `getattr(ns, ..., default)`.
- **`list --resources`:** prints `{"resources": [...], "resourceTemplates": [...]}`. The default output stays the same, plus a stderr hint when resources exist.
- **New `read <server> <uri> [--args JSON] --out FILE`:**
  - Output: a single text content is written verbatim; a single blob as raw bytes; multiple contents as a JSON array of content dumps.
  - A template target is expanded from `--args`.
  - `McpError`/`ValueError` → message and exit 1, never a traceback.
- **`probe <server> <target> --resource [--args JSON]...`:**
  - A target containing `{` is a template, and each `--args` supplies its variables. A missing required variable is an error; `--args` on a static URI is an error.
  - The part is keyed by the template or the normalized URI, with `kind` set.
  - The probe goes through `read_resource`, the same path as runtime: `summarize_shape(bytes)` → `"bytes"`, so no raw bytes reach shapes.
  - `--save-raw` base64-encodes bytes.
  - No discriminator advisory.
  - New `_part_filename(key)`: the quoted name when it is ≤200 characters, else a truncated name plus a sha256 suffix. Tool part names stay unchanged.
- **`merge`:** extract the stale-entry warning (~626–637) into `_stale_entries(carried, merged, manifest_doc)`. Entries with a `kind` (or a `:` in the key when `kind` is missing) are checked against the matching manifest section and skipped if that section is untracked. Without this, every carried resource entry would be flagged as a retired tool.
- **`codegen`:**
  - `--resources {all,templates,none}` passes the inventory to `render_module` and `manifest.build`.
  - Warns above 50 static resources.
  - `McpError` → exit 1 with a `--resources none` hint.
- **`check`:** `live = build(server, inv.tools, inv.resources, inv.templates)`; `--update` writes the sections.

## Task order (TDD, each commit independent)

Tasks 4–6 are pure and can run in parallel with 2–3.

1. **Seam protocol.** `tests/test_seam.py`: runtime-checkable, `McpCaller` signature unchanged.
2. **Bridge read.** `parse_resource`, `normalize_resource_uri`, `read_resource`/`_read`. Tests in `tests/test_bridge.py`:
   - json / repr / plain text / blob bytes / several contents / empty raises
   - one-shot session
   - block reuse alongside a tool call
   - `AnyUrl` passed to the SDK
   - caller satisfies both Protocols
3. **Bridge listing.** `list_resource_inventory`. Tests:
   - no capability → no requests
   - cursor followed
   - repeated cursor raises
   - templates METHOD_NOT_FOUND → empty
   - other `McpError` propagates
   - URI normalized
4. **URI templates.** New `tests/test_uritemplate.py`:
   - table of RFC 6570 examples (levels 1–4, scalars)
   - `None` is undefined
   - `+`/`#` keep reserved characters
   - malformed templates raise
   - required/optional classification
   - the emitted `_EXPAND_URI` matches the library on the same table
5. **Codegen accessors.** Tests in `tests/test_codegen.py`:
   - byte-identical output without resources
   - URI-derived names and de-duplication against tool names
   - static accessor exec'd against a fake reader
   - template expansion (`"Delivery Unit"` → `%20`)
   - optional query variable omitted
   - unwrap + model emits `_dig` + TypedDict
   - resources-only module imports only the reader Protocol
   - malformed template skipped with warning
   - docstring injection-safe
   - variable named `caller` renamed
   - `kind` only when given
6. **Manifest.** Tests in `tests/test_manifest.py`:
   - sections omitted when not collected; empty sections kept
   - added/removed → drift; mimeType → drift; name/description → advisory; template added → drift
   - untracked + live non-empty → advisory; untracked + live empty → silent
   - tracking is per section
   - `to_json` has no resources block when untracked
   - `render_text` resource lines
   - a non-dict section is rejected
7. **CLI inventory plumbing.** Tests in `tests/test_cli_list.py`: hook runs inside the session; one session; scope `none` skips the hook; a patched `_list_tools` → `None`.
8. **`list --resources`.** Tests: JSON output, default output unchanged plus the hint, no capability → empty.
9. **`read`.** New `tests/test_cli_read.py`: text verbatim, blob bytes, multi-content JSON, template args, static + `--args` errors, `McpError` → exit 1.
10. **`probe --resource`.** Tests in `tests/test_cli_probe.py`:
    - static part keyed by URI with `kind`
    - template keyed by template, with the variables in `probed_args`
    - missing variable errors
    - blob shape is `"bytes"`
    - long key → hashed filename
    - key normalized
    - tool part filename unchanged
11. **`merge` resource-aware stale check.** Tests in `tests/test_cli_merge.py`:
    - resource entries and verify.json preserved by key
    - template `probed_args` scrubbed
    - tracked resources not flagged
    - a resource missing from the manifest is flagged
    - untracked section skipped
12. **`codegen` wiring.** Tests in `tests/test_cli_codegen_manifest.py`:
    - accessors plus manifest sections
    - `templates` / `none` scopes
    - resource shapes applied
    - more than 50 statics warns
    - `McpError` hint
13. **`check` wiring.** Tests in `tests/test_cli_check.py`:
    - resource added → exit 1
    - old manifest + live resources → advisory, exit 0
    - `--update` adds the sections
    - JSON resources block
14. **Integration.**
    - Fixtures: `tests/integration/fixtures/stdio_server.py` gets `demo://config` (JSON), `demo://logo` (bytes → blob) and `demo://records/{record_id}`. `stdio_server_changed.py` changes `config`'s mimeType, adds `demo://status` and removes `logo`.
    - Tests in `test_stdio_e2e.py`:
      - real `list --resources`
      - `check` reports resource drift
      - manifest byte-stable
      - unknown-resource `read` → exit 1 with no traceback (also verifies anyio doesn't wrap `McpError` in an ExceptionGroup)
    - Tests in `test_generated_wrapper_e2e.py`:
      - static and template accessors
      - blob → bytes
      - resource + tool share one `connected()` block
      - probe → merge → regenerate gives a typed resource
15. **Skills and docs.**
    - `skills/generate-mcp-wrappers/SKILL.md`:
      - Step 0: floor → 0.11.0.
      - Step 2a: `list --resources`.
      - Step 2b: resources are read-only. Pick templates and API-like statics; skip data-like lists and log them in `session-overview.md`.
      - Step 3: `probe --resource` with real variable values.
      - Step 4: blobs stay `Any`.
      - Step 7: mention the accessors in the hand-off.
    - `references/shape-spec.md`: a "Resource entries" section covering keys, `kind`, allowed fields, raw-variable `input_overrides`, no discriminators, and the fact that URIs are not scrubbed.
    - `skills/generate-mcp-runner/SKILL.md`:
      - Floor 0.11.0.
      - Find `read_*` accessors via the `Resource:` docstring line and include them by default.
      - Template args come from verify.json.
      - Order a resource before the tool whose description says to read it first.
      - Never call `caller.read_resource` directly.
    - `README.md` (What you get, Command reference, Drift detection).
    - `doc/USAGE.md`: commands, a resources subsection under "Using the generated wrappers", fix the "exactly three keys" manifest sentence (~574), check section.
    - `CHANGELOG.md` `[Unreleased] — 0.11.0` Added/Changed. Changed: manifests gain resource sections on the next regenerate.
    - `CLAUDE.md`: update the seam description ("single McpCaller Protocol" becomes two Protocols).

Release (separate, via the `releasing-a-version` skill): engine 0.11.0 → plugin 0.6.0 (floor raise). Engine tag first.

## Out of scope (record in doc/DEFERRED.md; note that the file is currently untracked)

Subscriptions, `listChanged`, prompts, resource annotations (audience/priority), `tools/list` pagination, a `return_type` key for scalar/`bytes` returns, mimeType-gated text parsing (today `text/markdown` "123" becomes an int via literal_eval), and a multi-content return that keeps per-content URIs.

## Verification

- Gate on every task: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
- E2E, local: `uv run pytest tests/integration` against the FastMCP fixture with resources.
- Live against `delivery_central` (`~/.mcpgen/servers.json`; the config path is passed explicitly):
  1. `mcpgen list delivery_central --resources` → 2 resources + 1 template.
  2. `mcpgen read delivery_central risk://dictionary --out /tmp/…` → JSON text.
  3. `mcpgen probe delivery_central 'dimension://by-unit-type/{unitType}' --resource --args '{"unitType":"PROJECT"}' --emit-shape dc/delivery_central.shapes.json`, then `merge`, then `codegen` → `read_dimension_by_unit_type(caller, *, unitType: str)` present and `ast.parse` OK.
  4. A scratch script calls `read_risk_dictionary(McpBridgeCaller(...))` → dict.
  5. `mcpgen check delivery_central` → exit 0; then hand-remove a resource from the manifest → exit 1.
