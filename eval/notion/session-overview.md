# Notion MCP — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:13:04Z
- **Duration:** 7m 12s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **28 tools**. Every tool carries `annotations`, so classification needed no
keyword guessing: 14 are `readOnlyHint: true`, 14 are writes (four of them `destructiveHint`).
All 14 writers were skipped without probing. Two read tools were dropped as well —
`notion-download-attachment` and `notion-get-async-task` both require an id that only a
mutating tool can mint. That left **12 tools probed**, all live over hosted HTTP with ≥2 s
between calls. No seed commands were configured.

## Discriminators

`list --schema` raised 13 candidates, but 11 of them span mutating tools only and were recorded
unresolved rather than probed. `page_size` was auto-disqualified as pagination. The description
sweep found the one that mattered: **`query_type` on `notion-search`**, invisible to the
advisory because it lives on a single tool. Both enum values were probed separately and the
shapes genuinely differ — `internal` returns ranked records (`id/title/url/type/highlight/
timestamp`), `user` returns a single blob whose `text` holds a `<users-search-results>` XML
document. Confirmed, so it was resolved with `discriminator` + `variants`; codegen emitted two
`@overload`s over `Literal['internal'|'user']` plus a union impl.

`notion-query-data-sources` is also polymorphic (`data.mode` = `sql` | `view`), but the selector
sits nested inside an object rather than as a top-level scalar, so overloads cannot key on it —
resolved as a generic base model per option 2.

## Surprises

- `notion-query-meeting-notes` and `notion-search-agents` returned only errors — a Business-plan
  upsell and an `entitlement_required` validation error respectively. Both carry
  `_probe_status: "inconclusive"`; nothing about their real shape was observed.
- Three read tools returned empty containers on this workspace: `get-comments` (`{}`),
  `get-teams` (both team lists empty), `list-shared-pages` (`results: []`). Their inner shapes
  are unobservable, so nothing was invented — `get-teams` keeps hand-added `"list"` markers,
  `list-shared-pages` is unwrap-only `Any`.
- `notion-fetch` was probed twice, on a page id and a database id, to test whether `id` switches
  shape. It does not: four keys both times.

## Shape decisions

| Tool | unwrap | model |
|---|---|---|
| `notion-fetch` | — | `NotionEntity` |
| `notion-get-users` | `results` | `list[UserSummary]` |
| `notion-list-favorite-pages` / `-private-pages` | `results` | `list[SidebarPageSummary]` (identical fields, shared) |
| `notion-list-recent-pages` | `results` | `list[RecentPageSummary]` (adds `icon`) |
| `notion-get-teams` | — | `TeamsResult` |
| `notion-query-data-sources` | — | `DataSourceQueryResult` (only `has_more`; rows are per-database columns) |
| `notion-search` | `results` | `list[SearchContentItem]` / `list[SearchUserItem]` |

The regenerated module (140 KB, 28 wrappers) parses cleanly under `ast.parse`, and the seven
shaped tools dig their envelopes through `_dig_list`. `run.py` generation was left to the
harness verify stage.
