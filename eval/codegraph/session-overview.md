# codegraph — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:39Z
- **Duration:** 10m 10s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list codegraph --schema` reported **5 tools**, all read-only: `codegraph_search`,
`codegraph_context`, `codegraph_node`, `codegraph_explore`, `codegraph_trace`. None carried
`annotations`, so classification fell back to the keyword plus semantic read — every name and
description describes a query over an index, with no write or delete verb among them.
**All 5 were probed; 0 skipped.** No seed commands were configured, and none were needed: the
server indexes the working repository, which already held 16 files.

The server's own MCP instructions advertise five tools that do not exist in `tools/list`
(`codegraph_callers`, `codegraph_impact`, `codegraph_files`, …). Per the skill's guard, only the
tools `list` returned were probed.

**Discriminators: N/A.** The `list` advisory flagged `projectPath` across all five tools, but
Pass 1 auto-disqualifies it by name — it sits on the path-identity list beside `filePath`.
Nothing else is shared under one name with a scalar top-level type, so no candidate reached
Pass 2.

## Probe results and shape decisions

Every one of the five probes returned a substantive success payload (457–14,678 bytes), and
every one observed as `"str"`. Raw captures via `mcpgen call --out` confirmed why: the server
emits **human-readable Markdown**, not JSON. Each payload opens with a heading —
`## Search Results (5 found)`, `## Code Context`, `## verify_server (function)`,
`## Exploration: …`, `## Trace: verify_server → check_ast`. The guarded JSON-in-string test
returned `NOT_JSON` (JSONDecodeError) for all five, so there is no double-encoded record to
unwrap.

`codegraph_trace` was probed twice. The first pair (`cmd_verify` → `verify_server`) returned the
documented "no static call path" message; a second probe over a real edge harvested from
`codegraph_node` (`verify_server` → `check_ast`) returned a resolved 2-hop chain. Both are
Markdown prose, confirming the shape does not vary with success or failure.

Shape decision for all five tools is therefore identical and honest: `unwrap: []`,
`return_model: null`, `fields: {}` — **no shaped tool by design**. There is no vendor envelope,
no key path, and no record to promote to a `TypedDict`. Inventing an unwrap path here would make
the wrapper claim a dict it never returns. `_observed_shape: "str"` is retained as evidence;
`_probe_status: inconclusive` was deliberately **not** set, since every probe observed a genuine
result rather than an error.

One recon `codegraph_search` call harvested real symbol names, so no probe relied on an invented
identifier. `probed_args` hold only symbol names and free-text queries — no paths, ids, or PII —
so the post-merge scrub was a no-op.

## Generation

The regenerated module parses cleanly (`ast.parse` OK, 8,596 bytes). All five wrappers read
`-> Any` — the correct outcome for a prose-returning server. Two mechanical details landed
right: `kind` became `Literal['function', 'method', …]` from its enum, and the reserved word
`from` was escaped to `from_` in `codegraph_trace`.

Runner generation was left to the harness verify stage per the eval-harness rule, not performed
here.
