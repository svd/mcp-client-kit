# codegraph — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:28Z
- **Duration:** 3m 11s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

`codegraph` exposes **5 tools**, all read-only: `codegraph_search`, `codegraph_context`,
`codegraph_node`, `codegraph_explore`, `codegraph_trace`. The server sets no
`annotations`, so the step-2b keyword fallback decided the verdict: no tool name carries
a mutating whole word, and no description hints at a write.
**All 5 selected, all 5 probed, 0 skipped.** Local
`stdio` transport, so the full set was kept rather than pruned, and probes went out as a
single unpaced batch.

## mutating-skipped

None.

## Discriminators

`mcpgen list --schema` raised one candidate: `projectPath`, spanning all five tools.
Pass 1 auto-disqualifies it by name — it is on the path-identity list. It selects *which*
indexed project answers, never the response's shape. Nothing survived Pass 1, so Pass 2
made no live calls. **discriminators: N/A.**

## Probe results — the surprise

Every one of the five tools returned `_observed_shape: "str"`. That is not a probe
failure and not an empty store: each payload was a substantial, successful response
(466 B for `codegraph_context`, 1.2 KB for `search` and `node`, 12.4 KB for `trace`,
14.7 KB for `explore`). Raw payloads were captured with `call --out` and run through the
JSON-in-string guard — all five came back **`NOT_JSON`**. The bodies are hand-formatted
Markdown reports (`## Search Results (10 found)`, `### verify_server (function)`,
`**Location:** eval_harness/verify.py:691`), authored for a model to read rather than a
client to parse. No `_probe_status: inconclusive` marker was added anywhere: nothing was
inconclusive, the shape was observed and it is genuinely a string.

One oddity: `codegraph_context` returned the *smallest* payload of the five — the server
detected a small project (16 indexed files) and returned an advisory steering the caller
away from a follow-up `codegraph_explore` call instead of a fuller context dump.

## Shape decisions

There is no envelope to strip and no record to type. For all five tools:
`unwrap: []`, `return_container` unset, `fields: {}`, `return_model: null` — so every
wrapper honestly stays `-> Any`. Inventing a `TypedDict` over prose would state an
authoritative lie about a shape that does not exist. `_observed_shape` was kept as
evidence of *why* each entry is null. This is the `no_shaped_tool_by_design` case, not a
coverage gap.

The one substantive edit was `input_overrides`: `limit`, `maxNodes`, and `maxFiles`
declare JSON Schema `number` but are result counts with integer defaults (10, 20, 12), so
each was overridden to `int` rather than shipping a `float` signature.

Two things codegen got right unprompted: `codegraph_trace`'s `from` parameter — a Python
keyword — renders as `from_` and maps back to the wire key `"from"`; and
`codegraph_search`'s `kind` enum renders as a `Literal[...]` of its eight node kinds.

## Verification

`ast.parse` clean after both regenerations. `probed_args` hold only symbol names and
free-text queries — no emails, UUIDs, tokens, or personal names — so the post-merge scrub
had nothing to replace. `run.py` is the harness's own verify stage's job and was not
generated here.
