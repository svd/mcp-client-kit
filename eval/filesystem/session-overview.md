# filesystem — session overview

## Run Metadata

- **Executed:** 2026-08-27T11:03:14Z
- **Duration:** 3m 22s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **14 tools**. Every tool carries `annotations`, so mutating classification
needed no keyword or semantic fallback: `readOnlyHint: false` on `write_file`, `edit_file`,
`create_directory`, and `move_file` — all four skipped, never called. `read_media_file` is
read-only but returns base64 image/audio or an embedded resource, so per the media guard it was
left unprobed and unmodelled rather than typed from an envelope the probe never sees.

That left **9 tools probed**, all successfully: `read_file`, `read_text_file`,
`read_multiple_files`, `list_directory`, `list_directory_with_sizes`, `directory_tree`,
`search_files`, `get_file_info`, `list_allowed_directories`. Local stdio, so the full read-only
set was kept and probes were batched in one shell invocation with no pacing. No seed commands
were configured and none were run; `/private/tmp` already held enough files and directories to
probe against.

## Discriminators

The `list --schema` advisory named `head` and `tail` (spanning `read_file` / `read_text_file`).
Both are Pass 1 auto-disqualified as window parameters — they truncate the returned text, they do
not switch its shape. The description sweep turned up one more candidate, `sortBy` on
`list_directory_with_sizes` (`enum: ["name", "size"]`), also disqualified as a sort parameter.
**Verdict: discriminators N/A**, so Pass 2 was skipped.

## Shape decisions

Eight of the nine probed tools returned prose text: `_observed_shape: "str"`. `get_file_info`
returns `size: 206\ncreated: …\npermissions: 644` — line-oriented prose, confirmed `NOT_JSON`
against the raw payload, not a JSON object. `list_directory` returns `[FILE]`/`[DIR]` prefixed
lines. These are honest `str` returns, not probe failures, so no `_probe_status: inconclusive`
markers were recorded and every entry keeps `return_model: null`.

`directory_tree` was the one interesting case. Its payload is a **JSON-encoded string** — a
`JSON_UNWRAP list` of 29 `{name, type}` entries, with directories additionally carrying
`children`. But the parsed object *is* the record: there is no envelope key to unwrap to. Since
`_dig_list` is only emitted for a non-empty `unwrap`, inventing a path would have made the
wrapper claim a dict it never returns, so `unwrap` stays empty and `return_model` stays null,
with `_json_unwrap: true` and a note recorded as evidence for the next reader.

Net: **zero shaped tools, by design** — this server returns prose, not records.
`probed_args` were scrubbed (the machine-specific scratchpad path became `/private/tmp/probe-dir`).
The regenerated module parses cleanly under `ast.parse`; `sortBy` renders as
`Literal['name', 'size']` automatically. `run.py` is the harness verify stage's job, not this run's.
