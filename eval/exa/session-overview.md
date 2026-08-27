# exa — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:07:11Z
- **Duration:** 2m 24s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list exa --schema` reported **2 tools**, both carrying explicit
`annotations.readOnlyHint: true` / `destructiveHint: false`:

- `web_search_exa` — semantic web search, returns clean text from top results
- `web_fetch_exa` — reads one or more URLs as clean markdown

No mutating tools exist on this server, so nothing was skipped for safety. Both tools were
selected and probed: **2 probed, 0 skipped**. The server is hosted HTTP, so probes ran
sequentially with a 2 s pause between them rather than fanning out.

## Discriminators

`discriminators: N/A`. The `list --schema` advisory was silent, and the precondition confirms
why: no scalar parameter name is shared by two or more tools. `query` and `numResults` belong
only to `web_search_exa`; `urls` (an array) and `maxCharacters` only to `web_fetch_exa`. The
description sweep found no prose declaring a response key either, so Pass 2 was skipped.

## Probe results

Both probes returned successfully with substantial payloads — 24,857 bytes for
`web_search_exa` (query: a natural-language description of an MCP architecture article,
`numResults: 3`) and 1,601 bytes for `web_fetch_exa` (one public docs URL,
`maxCharacters: 1500`). Both observed as `"str"`.

Following the JSON-in-string check, each raw payload was captured with `mcpgen call --out` and
tested with a guarded `json.loads`. Both returned **`NOT_JSON`**: the search payload is a
`Title:/URL:/Published:/Author:/Highlights:` markdown block, and the fetch payload is plain
page markdown. These are genuine prose responses, not double-encoded records and not error
strings — so `_observed_shape: "str"` stands as honest evidence and no `_probe_status:
inconclusive` marker was warranted.

## Shape decisions

Neither tool is shapeable. For both: `unwrap: []`, `return_model: null`,
`return_container` unset, `fields: {}`, `source: "live"`. There is no vendor envelope to strip
and no record to promote — inventing an unwrap path here would make `_dig` return a fragment
the wrapper never receives. This is Mode A by design: a search API whose contract is text.

`probed_args` needed no scrubbing — a generic technical query and a public documentation URL,
both functional and PII-free.

## Verification

Regeneration with the shape-spec in place produced a byte-identical 3,310-byte module.
`ast.parse` succeeded; both wrappers read `-> Any`, which is the correct and honest signature
for tools that return prose. `run.py` is the harness's responsibility and was not generated here.
