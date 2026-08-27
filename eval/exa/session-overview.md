# exa — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:42:15Z
- **Duration:** 4m 34s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

`mcpgen list exa --schema` reported **2 tools**, and **both were probed**; none were skipped.
Both carry an explicit `annotations.readOnlyHint: true`, so classification was a clean annotation
read — no keyword or semantic fallback needed, and no tool withheld from the selected set.

- `web_search_exa` — semantic web search; `query` required, `numResults` optional
- `web_fetch_exa` — pages as markdown; `urls` required, `maxCharacters` optional

**Discriminators: N/A.** No parameter name is shared by two or more tools (`query`, `numResults`,
`urls`, `maxCharacters`), so the candidate precondition cannot be met and no advisory fired on the
`list` stderr. Pass 2 was correctly skipped rather than run and recorded inconclusive.

## Probing and responses

Both were probed live, sequentially with ≥2 s pacing, each twice with distinct argument variants
to widen the observation rather than type from one sample:

- `web_search_exa` — a natural-language query with `numResults: 3`, then a `category:company`
  query with `numResults` omitted.
- `web_fetch_exa` — one URL with `maxCharacters: 2000`, then two URLs batched, `maxCharacters`
  omitted.

Every probe returned a real success payload (24.8 KB of search results, 5.4 KB of fetched
markdown), so nothing here is an error shape and no `_probe_status: inconclusive` marker applies.

Both observed as `"str"`. Because a bare `"str"` can hide a double-encoded record, both raw
payloads were captured with `mcpgen call --out` (one call per invocation) and tested with the
guarded `json.loads` check. Both returned **NOT_JSON** — genuine prose. Search results arrive as a
flat `Title: / URL: / Published: / Author: / Highlights:` markdown block per result; fetches
arrive as page markdown under an `# <title>` heading. The one mild surprise: batching two URLs
into `web_fetch_exa` still returns a *single* concatenated markdown string rather than a list or
per-URL object — the byte count grew (2106 → 5351) while the shape did not.

## Shape decisions

Neither tool was shaped — the correct outcome, not a gap:

- `web_search_exa` — `unwrap: []`, `return_model: null`, `fields: {}`. No vendor envelope to dig
  through and no record to promote; the payload is human-readable text end to end.
- `web_fetch_exa` — identical, and confirmed against the multi-URL variant.

`_observed_shape: "str"` is retained in both entries as evidence that these are genuine
text-returning tools, matching the other prose-returning servers in this eval set. `probed_args`
needed no scrubbing: public search queries and public documentation URLs, no PII, and functional
(the roundtrip verifier replays them).

This lands exa in **Mode A — no shaped tools by design**, consistent with the manifest note.

## Final module

Regeneration picked up the shape-spec (`[codegen] shapes: … (2 tool(s))`) and correctly emitted
both wrappers as `-> Any` with no `_dig`/`_dig_list` helpers — the engine did not invent an unwrap
where none was recorded. The module parses cleanly under `ast.parse`. Per the harness contract,
`run.py` was not generated here; the verify stage owns it.
