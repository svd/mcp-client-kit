# filesystem — Session Overview

## Run Metadata

- **Executed:** 2026-08-25T15:42:31Z
- **Duration:** 2m 22s

## Summary

The `filesystem` server (`@modelcontextprotocol/server-filesystem`, scoped to
`/private/tmp`) exposes 14 tools. `annotations.readOnlyHint` was present and
trusted on every tool, cleanly splitting the set: 10 read-only tools were
probed, and 4 mutating tools (`write_file`, `edit_file`, `create_directory`,
`move_file`) were skipped per the subagent fallback (probe all non-mutating,
skip mutating entirely — no `AskUserQuestion` available in this run).

`mcpgen list --schema` flagged `head` and `tail` (shared by `read_file` and
`read_text_file`) as discriminator candidates, but both match the pagination/
window auto-disqualify list (Pass 1) and were dropped without probing —
they're read-window offsets, not response-shape switches. `read_text_file`
was still multi-probed across a default call plus `head=3` and `tail=3` to
confirm the return type doesn't vary with those args.

Real files already present in the shared `/private/tmp` scratch directory
were used as probe targets (`rem.py`, `serve.out`, and a small `.png` under
a `claude-501` session scratchpad) rather than writing new fixtures, since
all 10 selected tools are read-only.

## Shape decisions

Nine of the ten probed tools return **plain text**, matching this server's
convention of formatting output as human-readable strings rather than JSON:
`read_file`, `read_text_file`, `read_multiple_files`, `list_directory`,
`list_directory_with_sizes`, `search_files`, `get_file_info`,
`list_allowed_directories`. All kept `return_model: null` (`-> Any`) — an
honest reflection of a genuine text return, not an under-probed shape.

`directory_tree` was the one surprise: its content is a JSON-encoded string
representing a recursive `{name, type, children}` tree (49+ nested entries
observed on a real subtree). Per the "don't model depth from one probe"
guard, a recursive tree structure isn't representable as a flat `TypedDict`
regardless of sample count, so it was left `unwrap: []`, `return_model: null`
— callers that want the parsed tree can `json.loads()` the string themselves.

`read_media_file` was the only tool that shaped into a real record: mcpgen's
probe engine recognized the MCP binary-content envelope and reported
`{type: str, mimeType: str, has_data: bool}` (base64 payload redacted behind
the `has_data` boolean). This became `return_model: "MediaFile"`,
`unwrap: []`, `return_container` omitted (single dict, not a list).

`probed_args` for `read_media_file` contained a probe file path with a
personal username and a session UUID segment; it was scrubbed to
`<example-dir>/img.png` with `probe_args_scrubbed: true` — the gitignored
`filesystem.verify.json` sidecar retains the real path for the roundtrip
verifier. All other probed paths (`/private/tmp/rem.py`, `/private/tmp`,
`/private/tmp/claude-501`, `/private/tmp/serve.out`) are generic scratch
paths with no PII and were left as-is.

## Result

The regenerated module (`filesystem.py`, 14 tools, 12.9 KB) parses cleanly.
`eval-kit verify filesystem` passed all five checks — `ast`, `signatures`,
`idempotency`, `pii`, and `roundtrip` (a live call to `read_media_file`
returned the typed `MediaFile` record).
