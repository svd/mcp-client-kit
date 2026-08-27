# memory — session overview

## Run Metadata

- **Executed:** 2026-08-27T11:03:14Z
- **Duration:** 3m 43s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Seeding

Both seed commands ran before the skill was invoked and both exited 0, each returning `[]`.
The empty arrays are not a failure: the server persists its graph to disk, so the entities
and the relation already existed from an earlier run and `create_*` is a no-op for
duplicates. A confirming `read_graph` call showed both entities (`Ada Lovelace`,
`Analytical Engine`) and the `programmed` relation present, so probing ran against a
populated store rather than an empty one.

## Tools and selection

The server exposes **9 tools**. Every one carries explicit `annotations`, so the
mutating classification needed no keyword or semantic fallback: `readOnlyHint: true` on
`read_graph`, `search_nodes`, and `open_nodes`; `readOnlyHint: false` on the other six,
with `destructiveHint: true` on the three `delete_*` tools. **3 probed, 6 skipped as
mutating.** The transport is local stdio, so the selected set was kept at every
non-mutating tool with no pruning.

No discriminator advisory fired on `list --schema`, and that matches the schemas: every
shared parameter is an array type, and the only string scalar (`query`) is both on the
engine denylist and confined to one tool. A description sweep found no parameter naming a
response key either, so `discriminators: N/A` and Pass 2 was skipped.

## Surprises

The interesting finding is that all three read tools return the **same** envelope —
`{"entities": [...], "relations": [...]}` — rather than the narrower payloads their names
suggest. `open_nodes` and `search_nodes` return a filtered *subgraph*, including the
relations among the matched nodes, not a bare list of nodes. `search_nodes(query="Ada")`
returned one entity where the other two returned two, confirming filtering works while the
structure stays fixed.

## Shape decisions

All three tools: `unwrap: []` — there is no vendor envelope, the payload is already the
record. Because the shape is byte-for-byte identical across the three, they share one
return model, `KnowledgeGraph`, which is permitted since their `fields` dicts match.
`return_container` is omitted: the record is a single dict, not a list.

`fields` is `{"entities": "list[dict]", "relations": "list[dict]"}`. The element shapes
were observed (`name`/`entityType`/`observations`, and `from`/`to`/`relationType`), but
those sit at level 2 and the shape-spec has no way to declare auxiliary models, so
`list[dict]` is the honest ceiling rather than a fabricated nested type.

`probed_args` was left unscrubbed: `Ada Lovelace` and `Analytical Engine` are synthetic
fixture names from the harness's own seed commands, already checked into `servers.toml`,
and identify no real person.

## Result

The regenerated module parses cleanly (`ast.parse` OK). All three read-only tools read
`-> KnowledgeGraph` and cast the call result; the six mutating tools correctly remain
`-> Any`, never having been probed.
