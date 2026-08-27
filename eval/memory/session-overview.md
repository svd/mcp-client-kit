# memory — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:40Z
- **Duration:** 9m 44s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Seeding

Both seed commands ran before the skill and both succeeded (exit 0), but each returned `[]`
rather than the created records: the server had been seeded by an earlier run and
`create_entities` / `create_relations` return only the *newly* created items, deduping the
rest. A `read_graph` check confirmed the store did hold the two entities (Ada Lovelace,
Analytical Engine) and the one `programmed` relation, so probing proceeded against real data
rather than an empty graph.

The folder also held artifacts from that earlier run, including a `memory.shapes.json` that
`codegen` auto-detected on the first pass. Those were moved aside and the module regenerated
from bare stubs, so this record reflects one run only.

## Tools and selection

The server exposes **9 tools**. Every one carries explicit `annotations`, so classification
needed no keyword or semantic fallback: `readOnlyHint: true` on `read_graph`, `search_nodes`,
and `open_nodes`; `readOnlyHint: false` on the six mutators (`create_entities`,
`create_relations`, `add_observations`, plus the three `delete_*`, which also set
`destructiveHint: true`). All three read-only tools were probed; the six mutating tools were
skipped and left `-> Any`, which suits them — they return acks, not records.

**Discriminators: N/A.** No parameter is shared by two or more tools under the same name with
a top-level scalar `type` — every multi-tool parameter (`entities`, `relations`, `names`,
`deletions`) is an array, and `query` is both single-tool and denylisted. The `list --schema`
stderr carried no advisory, matching that reading.

## Surprises

`search_nodes` with `query: "Ada"` returned one entity but *also* the Ada→Analytical Engine
relation, whose other endpoint is absent from the returned `entities`. The server filters
entities by the query and then returns relations touching any match, so callers can receive
relations pointing at nodes not present in the same payload. Worth knowing; it does not change
the type.

## Shape decisions

All three read tools returned the identical top-level record `{entities: [...], relations: [...]}`
with **no vendor envelope**, so `unwrap` is `[]` for each and no `_dig` helper is emitted.
Because the three `fields` dicts are identical, they legitimately share one model,
`KnowledgeGraph` — which is also the honest domain name, since each returns a subgraph.
`return_container` is omitted: the record is a single dict, not a list.

`fields` records `entities` and `relations` as `list[dict]`. The element shapes were observed
and stable (`name`/`entityType`/`observations`, `from`/`to`/`relationType`), but `fields` is a
flat map and nested `TypedDict`s are not expressible there, so the inner dicts stay `dict`
rather than over-claiming depth from a two-entity store.

## Verification

The regenerated module parses cleanly (`ast.parse` OK). All three shaped tools read
`-> KnowledgeGraph` rather than `-> Any`; the six mutating tools remain `Any` as intended.
`run.py` is the harness's job and was not generated here.
