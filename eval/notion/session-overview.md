# Notion — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:49:11Z
- **Duration:** 7m 18s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Inventory and selection

`mcpgen list notion --schema` returned **28 tools**. Notion supplies full `annotations`, so
classification never fell back to keyword heuristics: 14 tools carry `readOnlyHint: true`, and the
other 14 (`create-pages`, `update-page`, `move-pages`, `create-view`, …) were skipped as mutating
and never called. Two read-only tools were also dropped because their only required argument is an
id that a mutating tool must mint first: `download-attachment` needs a `file_upload_id` from
`create-attachment`, and `get-async-task` needs a `task_id` from an async write. That left
**12 selected tools**, probed serially with a 2 s pace because the endpoint is hosted HTTP.

## Discriminators

The `list` advisory raised 13 candidates. `page_size` fell to Pass 1 as pagination; ten more span
only mutating tools and are recorded unresolved rather than probed. Two reached Pass 2 inside the
selected set. `page_id` on `get-comments` produced identical shapes across two pages — inconclusive,
not disproven. `page_url` on `search` returned an empty result list, so nothing was established.

The real discriminator was one the advisory could not see, because only one tool declares it:
`query_type` on `search`. `internal` returns `results[{id, title, url, type, highlight, timestamp}]`;
`user` returns `results[{text}]`. Both enum values were probed, so option 1 applies — the spec emits
`discriminator` + `variants`, and codegen renders two `@overload`s returning `list[SearchContentItem]`
and `list[SearchUserItem]`. One cost: the impl signature now types `query_type` as required, though
the server schema makes it optional.

`fetch` is polymorphic on `id` in a milder way — `id: "self"` adds a `self` object to the envelope,
while page, database, and data-source ids all return the same four top-level keys. A magic-string id
cannot be an enumerable `Literal`, so this took option 2: one `NotionEntity` base model
(`total=False`) over the unioned probe.

## Shape decisions

`search`, `get-users`, and the three sidebar list tools unwrap `results` into `list[...]`.
`list-private-pages` and `list-favorite-pages` returned byte-identical record shapes and share
`SidebarPageSummary`; `list-recent-pages` also carried `icon`, so it got its own `RecentPageSummary`
rather than widening a shared name. `get-teams` and `query-data-sources` keep their envelope
(`unwrap: []`) — the former because both team arrays came back empty, the latter because row keys are
the data source's own column names ("Name", "Status") and no honest `TypedDict` describes them.

Three tools were left deliberately untyped. `get-comments` returned `{}` on both pages — this
workspace has no discussions, so the record is unobservable, not absent. `query-meeting-notes` and
`search-agents` returned only entitlement errors (Business plan; Notion AI), so both carry
`_probe_status: "inconclusive"` rather than a misleading `"str"`.

The regenerated module parses cleanly under `ast.parse`, and every shaped signature reads its model
instead of `Any`.
