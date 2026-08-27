# filesystem — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:39Z
- **Duration:** 11m 39s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

`mcpgen list` reported **14 tools**. Every tool carried MCP `annotations`, so mutating
classification never needed the keyword fallback: `write_file`, `edit_file`, `create_directory`,
and `move_file` declare `readOnlyHint: false` and were skipped outright. Of the ten remaining
read-only tools, `read_media_file` was left unprobed — its description is explicitly about
base64 image and audio blocks, which the prober reduces to an envelope summary rather than the
record, so modelling it would state a shape no probe saw. That left **9 tools probed, 5 skipped**.

**Discriminators: N/A.** The `list --schema` advisory flagged `head` and `tail` as candidates
spanning `read_file` and `read_text_file`. Both are named in Pass 1's pagination/window
auto-disqualify list — they window a text response, they do not switch its shape — so Pass 2
made no live calls and no tool stayed polymorphic-suspect.

## Surprises

The interesting finding is how little of this server is shapeable, and that this is correct
rather than a gap. Eight of the nine probed tools return **human-formatted prose**, not records:
`get_file_info` answers with `size: 3845\ncreated: …` key-value text, `list_directory` with
`[DIR] x / [FILE] y` lines, `search_files` with newline-joined paths. A raw `call --out` capture
confirmed the `get_file_info` payload is genuinely not JSON, so `_observed_shape: "str"` is a
settled fact and not a probe failure — no `_probe_status: inconclusive` marker was warranted
anywhere in this run.

The batched probe sweep tripped the harness's 2-minute command ceiling on the ninth tool
(`list_allowed_directories`); it was re-issued alone and succeeded. Eight parts had already
been written, so nothing was lost.

## Shape decisions

- **`directory_tree` → `list[DirectoryNode]`** (`unwrap: []`, `return_container: "list"`). This
  is the one tool carrying a real record. The server double-encodes: the MCP text block *is* a
  JSON array. The seam's own `parse()` runs `json.loads` on text content, so the probe observed a
  parsed `[{name, type, children}]` — the list is what a caller actually receives, and the
  annotation is honest rather than a cast over a string. `unwrap` stays empty because the parsed
  object *is* the record: there is no envelope key to dig, and inventing one would make `_dig`
  return a field instead of the tree. `children` is held at `"list"` — the nodes recurse, and
  promoting a nested element model from one probe would over-state depth. Flagged `_json_unwrap`.
- **The other 8 probed tools → `Any`.** Prose in, prose out. A `TypedDict` over a formatted
  string would be a fabrication, so `return_model` stays `null` for each.

The regenerated module `ast.parse`s cleanly; `directory_tree` is the only signature that reads
anything other than `Any`.
