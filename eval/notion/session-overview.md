# notion — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T17:33:00Z
- **Duration:** 7m 0s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **28 tools**; 14 carry `annotations.readOnlyHint: true`. Because Notion is a
hosted HTTP endpoint every probe is serial and paced, so the read-only set was pruned to the
record-carrying tools. Two were dropped for a lack of reachable inputs, not for risk:
`notion-download-attachment` needs a `file_upload_id` that only the mutating `create-attachment`
produces, and `notion-get-async-task` needs a `task_id` minted by an async mutation. That leaves
**12 probed** and **16 skipped** (14 mutating, 2 unreachable). No mutating tool was called.

## Discriminators

`mcpgen list` flagged 13 candidates, but every one either spans only mutating tools or fails the
judgment test: `page_size` is pagination, `page_url`/`page_id`/`discussion_id` are identity
references. The two real discriminators came from the description sweep instead, each confined
to a single tool and therefore invisible to the cross-tool advisory:

- **`notion-search.query_type`** — confirmed. `internal` returns ranked content hits
  (`{id,title,url,type,highlight,timestamp}`); `user` returns one row whose `text` holds a
  `<users-search-results>` XML blob. Two enum values, both probed separately, scalar and
  optional → resolved with variant overloads plus an omission overload returning the union.
- **`notion-fetch.id`** — confirmed but unbounded. A page id and a database id return the same
  `{metadata,title,url,text}` envelope; `id="self"` returns a workspace/user identity payload
  under a `self` key. Values cannot be enumerated as `Literal`s, so this took the generic base
  model (`NotionEntity`, `total=False`).
- **`notion-query-data-sources.data.mode`** (sql | view) is a third shape switch, but it is
  nested inside an object parameter and `@overload` keys on top-level scalars only. Recorded as
  unresolvable; only SQL mode was probed.

## Surprises

Two tools returned no observable success payload and are marked `_probe_status: inconclusive`:
`notion-query-meeting-notes` (Business-plan upsell text) and `notion-search-agents` (a 400
`entitlement_required` envelope on **both** `scope` values). Three more returned genuine but
empty payloads — `notion-get-comments` gave `{}`, `notion-list-shared-pages` gave `results: []`,
and `notion-get-teams` gave two empty team arrays — so their inner element shapes are
unobservable. `list-shared-pages` was deliberately left untyped rather than copied from its
identically-shaped siblings.

## Shape decisions

The list tools (`get-users`, `list-private-pages`, `list-favorite-pages`, `list-recent-pages`,
`query-data-sources`) all unwrap `["results"]` into `list[Model]`. `private` and `favorite` share
`SidebarPageSummary` because their fields are identical; `recent` gets its own
`RecentPageSummary` for the extra `icon`. For `query-data-sources` only `id`, `url`, and
`createdTime` are Notion-supplied — every other row key is a user-defined column — so nothing
else was promoted. `get-teams` keeps the envelope (`TeamsResult`) since its lists are the payload.

The regenerated module parses cleanly (`ast.parse` OK), emits 8 `TypedDict`s, and the shaped
bodies dig via `_dig_list`. Probed args were scrubbed of workspace ids post-merge; the unscrubbed
twin stays in the gitignored `notion.verify.json`.
