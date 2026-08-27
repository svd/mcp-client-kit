# filesystem — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:28Z
- **Duration:** 3m 57s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Setup

`mcpgen` resolved to `uv run mcpgen` (0.9.0.dev1); every command carried
`MCPGEN_SERVERS=.mcp.eval.json`. No seed commands were configured. The ignore preflight
passed first try.

## Tool census

The server exposes **14 tools**, all carrying `annotations.readOnlyHint`, so step 2b's
primary path decided every classification and the keyword heuristic never ran.

### mutating-skipped

- `write_file` — `readOnlyHint: false`, `destructiveHint: true`
- `edit_file` — `readOnlyHint: false`, `destructiveHint: true`, `idempotentHint: false`
- `create_directory` — `readOnlyHint: false`
- `move_file` — `readOnlyHint: false`, `destructiveHint: true`

No `readOnlyHint: true` tool self-contradicted and no name disputed its hint, so all ten
read-only tools cleared. One was then dropped by the media rule: `read_media_file` returns
base64 image/audio blocks, whose probe would describe the envelope rather than the record,
so it stays `-> Any`, unprobed by design. **Probed: 9. Skipped: 5.**

## Discriminators

`list --schema` flagged `head` and `tail`, shared by `read_file` and `read_text_file`. Both
sit on Pass 1's pagination/window auto-disqualify list — they window the output, they do
not switch its shape — so both were dropped without a live call. **discriminators: N/A**;
Pass 2 did not run.

## Surprises

The finding is how little of this server is structured. Eight of nine probes came back as
bare `str`: `list_directory` and `list_directory_with_sizes` return `[FILE]`/`[DIR]`-prefixed
text, `search_files` newline-joined paths, and `get_file_info` *key: value* prose
(`size: 302`, `isDirectory: false`) that looks tabular but is not JSON. That verdict is
stronger than a substring guess — the bridge's `_parse_one` tries `json.loads` then
`ast.literal_eval` before falling back to text, so an observed `"str"` proves both failed.
Raw captures of `get_file_info`, `search_files`, and `list_allowed_directories` confirmed
each was a genuine success payload, not an error, so no `_probe_status: inconclusive` was
warranted. `directory_tree` is the lone exception: it ships a JSON-encoded string the
transport parses for us, so the probe saw a real `list` of `{name, type, children}` nodes.

## Shape decisions

- **`directory_tree`** → `unwrap: []`, `return_container: "list"`, `return_model:
  DirectoryNode` (`name: str`, `type: str`, `children: list`). There is no vendor envelope —
  the parsed payload *is* the record list — so no `_dig_list` is emitted and the wrapper
  casts directly. `children` stays a bare `list`: the nest is recursive, and modelling its
  elements from one probe would overstate what was seen. `total=False` covers files, which
  carry no `children`. Noted `_json_unwrap: true`.
- **The eight prose tools** → `return_model: null`, `unwrap: []`. A `TypedDict` over an
  unparsed string would be a lie. `get_file_info` is the tempting one, left alone because
  typing it needs parsing the wrapper does not do.

`probed_args` needed no scrubbing — every value is a `/private/tmp` path or a glob.

## Verification

The regenerated module parses cleanly (`ast.parse` OK). `directory_tree` reads
`-> list[DirectoryNode]`; the other thirteen stay `-> Any`, the honest signature for prose
and for unprobed mutating tools.
