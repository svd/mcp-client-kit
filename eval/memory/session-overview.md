# memory — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:28Z
- **Duration:** 3m 55s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Seeding

Both harness seed commands ran before the skill and both succeeded: `create_entities`
(Ada Lovelace, Analytical Engine) and `create_relations` (Ada Lovelace —programmed→
Analytical Engine). Seeding mattered: the read tools return
`{"entities": [], "relations": []}` against an empty store, leaving inner element shapes
unobservable. With the store populated, every probe carried real records.

## Tool selection

The server exposes **9 tools**. All nine carry `annotations`, and every one declares
`readOnlyHint`, so step 2b's primary path decided the whole set with no keyword guessing:

- **3 probed** (`readOnlyHint: true`, agreeing annotations, names clean under the
  contradiction check): `read_graph`, `search_nodes`, `open_nodes`.
- **6 skipped as mutating** (`readOnlyHint: false`, stated by the server): `create_entities`,
  `create_relations`, `add_observations`, `delete_entities`, `delete_observations`,
  `delete_relations`. The three `delete_*` tools also carry `destructiveHint: true`.

`discriminators: N/A` — `mcpgen list --schema` emitted no advisory, and no candidate could
clear the precondition: the only scalar parameter shared shape-wise is `query`, which is on
the engine's own denylist; every other parameter is an array.

## Probe results and shape decisions

All three probes returned success payloads on the first attempt. The surprise was the
absence of one: the memory server ships **no vendor envelope**. Each tool returns the
record directly as `{"entities": [...], "relations": [...]}`, so `unwrap` is `[]` for all
three and the generated bodies `cast(...)` rather than `_dig(...)`.

The three shapes are byte-for-byte identical in structure, which is why all three share a
single `return_model: "KnowledgeGraph"` — the skill permits a shared name exactly when the
`fields` dicts match, and here they do. `return_container` is omitted: the unwrapped value
is one dict, not a list of records.

`fields` records both top-level keys as `list[dict]`. Neither is a scalar, but both were
observed non-empty, and typing them `list[dict]` states the level that was actually seen
while leaving the element shape unmodeled, per the depth guard. Promoting `Entity` and
`Relation` to real nested models is not expressible — codegen only emits models named by
`return_model`.

One genuine oddity: `search_nodes(query="Ada")` returned a single entity (Ada Lovelace)
alongside a relation whose `to` endpoint, "Analytical Engine", is **not** in the returned
entity list. The server filters entities by the query but does not restrict relations to
edges between surviving nodes, so callers must treat returned relations as possibly
dangling. This is behavior, not shape.

`input_overrides` is empty — no schema lied. `probed_args` were left unscrubbed: the values
are harness-authored public fixtures ("Ada Lovelace", "Analytical Engine", "Ada") committed
already in `servers/servers.toml`, not user PII, and keeping them functional lets the
roundtrip verifier replay from the committed artifact.

## Verification

The regenerated module parses (`ast.parse`) and imports cleanly. All three shaped
signatures read `-> KnowledgeGraph`; the six mutating tools correctly remain `-> Any`.
Runner generation was left to the harness verify stage, as instructed.
