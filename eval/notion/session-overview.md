# notion — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:08:19Z
- **Duration:** 6m 42s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Coverage

The server exposes **28 tools** and annotates every one: 14 `readOnlyHint: true`, 14
`readOnlyHint: false`. No hint was disputed, so the annotations decided the selection
outright and the 14 mutating tools were skipped without a keyword judgment call.

**12 of the 14 read-only tools were probed.** The other two are unreachable by design:
`notion-download-attachment` needs a `file_upload_id` only the mutating
`create-attachment` returns, and `notion-get-async-task` needs a `task_id` only an async
mutating call produces. No seeds were configured, and none ran.

## Surprises

**The advisory missed the real discriminator and flagged decoys.** All 13 candidates
`mcpgen list` named were shared-parameter coincidences among mutating tools; the three
touching the selected set (`page_id`, `discussion_id`, `page_url`) are identity
references, not shape switches. The parameter that actually switches a shape,
`query_type` on `notion-search`, was invisible to the advisory because only one tool
declares it. Both enum values differ: `internal` returns ranked page
hits (`id`/`title`/`url`/`type`/`highlight`/`timestamp`), `user` returns person cards
carrying a single `text` blob. Confirmed, both variants observed, so overloads — not a
base model.

**Pass 2 on `page_id` was inconclusive.** Three distinct page ids on
`notion-get-comments` all returned the identical `{}`; a fourth with `include_resolved`
and `include_all_blocks` true also returned `{}`. None of those pages carries a
discussion. `page_url` on `notion-search` returned the same `{results, type}` envelope
with `results` empty — it scopes the search, it does not reshape it.

**Two tools are entitlement-gated.** `notion-query-meeting-notes` returned a plain-text
Business-plan upsell; `notion-search-agents` returned an `APIResponseError` object with
`tool_error_code: entitlement_required` on *both* `scope` values. Every response from
each was an error, so both carry `_probe_status: "inconclusive"`.

**The workspace is thin.** `notion-get-teams` returned both team lists empty and
`notion-list-shared-pages` returned `results: []` — envelopes established, elements not.

## Shape decisions

`notion-search` → unwrap `results`, overloaded on `query_type` into
`list[SearchResultItem]` / `list[SearchPersonItem]`. `notion-fetch` → no envelope,
`NotionEntity`; the same four keys held across a page id and a database id, and the
nested `metadata` dict is excluded per the depth guard. `notion-get-users` → `results` →
`list[UserSummary]`. `notion-query-data-sources` → `results` → `list[DataSourceRow]`,
carrying only `id`/`url`/`createdTime`, since every other row key is a user-defined
database column. `notion-list-private-pages` and `notion-list-favorite-pages` share
`SidebarPageEntry` — their observed field sets are identical, the one condition allowing
a reused name — while `notion-list-recent-pages` adds `icon` and so earns its own
`RecentPageEntry`. `notion-get-teams` → `TeamsResult`, both team lists kept as bare
`list` so callers still see them. The remaining four stay `Any`: `list-shared-pages` has
an established unwrap but an unseen element (withheld rather than borrowed from its
siblings), `get-comments` proved only that the tool answers, and the two gated tools
observed nothing.

## Verification

The regenerated module parses cleanly (`ast.parse` OK, 140,336 bytes) with eight
`TypedDict`s. The `notion-search` overloads render as `Literal['internal']` →
`list[SearchResultItem]` and `Literal['user']` → `list[SearchPersonItem]`, the
implementation returning the union. List-unwrapped bodies dig via
`_dig_list(result, ('results', ))`; the empty-unwrap tools cast directly. Real page ids
and the data-source UUID were placeholdered in `notion.shapes.json`; the gitignored
`notion.verify.json` holds the live args for roundtrip.
