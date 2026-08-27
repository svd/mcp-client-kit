# astro-docs — eval session overview

## Run Metadata

- **Executed:** 2026-08-27T06:05:18Z
- **Duration:** 1m 41s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Engine

Resolved `mcpgen` invocation: `uv run mcpgen` (0.9.0.dev1). The bare `mcpgen` form is
absent in this uv-managed project. Server reached through the config form,
`MCPGEN_SERVERS=.mcp.eval.json`, which maps `astro-docs` to the streamable-HTTP endpoint
`https://mcp.docs.astro.build/mcp`. No auth, no seed commands.

## Tool surface

`mcpgen list astro-docs --schema` returned exactly **one** tool:

```
Tools on astro-docs:
  search_astro_docs — Search the official Astro framework docs
```

The server supplies no `annotations` block, so the mutating check fell back to the
keyword heuristic: `search` leads the name, no mutating whole word appears, and the
description describes a read. Selected — 1 of 1 tools probed, 0 skipped, no
`mutating-skipped` entries.

**discriminators: N/A.** A candidate needs two or more tools declaring the same scalar
parameter; with a single tool the precondition cannot be met, and `query` sits on the
engine's own denylist regardless. Pass 2 was skipped outright.

## Probing

Three live probes, paced ≥ 2 s apart as the hosted-HTTP rule requires:

1. `query="view transitions"` — 23,866 bytes observed.
2. `query="zzzqxnonexistenttopic"` — 19,889 bytes observed.
3. `query="view transitions"` again, so the merged `probed_args` carries the meaningful
   query for the roundtrip verifier.

**The surprise was the nonsense query.** A term that matches nothing in the Astro docs
still returned ten results with an identical field set — the backend is a dense/vector
search that always fills its result window rather than returning `[]`. That is useful
for shaping: there is no empty-list path to leave an inner element shape unobservable,
and the `...x10` element count was identical across both queries, so the normalization
rule (ignore `...xN`) was not even needed to call the two shapes equal.

## Shape decision

One entry, one decision. The payload is a clean single-level envelope:

```
{"search_results": [{"content", "source_url", "title", "source_type"}, ...x10]}
```

- **unwrap:** `["search_results"]` — one vendor key strips to the record list.
- **return_container:** `"list"` — the unwrapped value is a list of records, so the body
  digs via `_dig_list` and defaults to `[]`.
- **return_model:** `AstroDocSearchResult` — a new capitalized name, no collision risk
  with only one tool in the module.
- **fields:** all four top-level scalars, every one observed as `str` in both probes and
  never null, so none is marked nullable. No depth was modelled beyond the record itself
  because there is none to model — the elements are flat.
- **probed_args:** `{"query": "view transitions"}`. Nothing here matches a PII pattern —
  a search string is a functional value — so the scrub pass changed nothing.

## Verification

The regenerated module parses cleanly under `ast.parse`, and the eval target holds:
`search_astro_docs` reads `-> list[AstroDocSearchResult]` rather than `Any`, and its body
digs `('search_results',)`. `run.py` is the harness verify stage's job and was not
generated here.
