# firecrawl — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:49:11Z
- **Duration:** 9m 32s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool census

The server exposes **26 tools**. `annotations.readOnlyHint` was present on all of them, so
classification needed no keyword or semantic fallback: 16 read-only, 10 mutating. The 10
mutating tools (`crawl`, `agent`, `interact`, `interact_stop`, `feedback`, `search_feedback`,
`monitor_create/update/delete/run`) were skipped entirely — no seeds were configured and the
subagent fallback forbids opting one in.

Of the 16 read-only tools, **11 were probed**. Five were not: `check_crawl_status` and
`agent_status` need a job id that only a mutating tool can mint, and `monitor_get`,
`monitor_checks`, `monitor_check` need a monitor id — `monitor_list` returned an empty `data`,
so the eval account has none. All five are recorded as unprobed, not as failures.

## Discriminators

`list --schema` flagged 11 scalar candidates. Pass 1 disqualified `k` and `maxAge` as
window/cache controls; `prompt`, `scrapeId`, `rating` and `querySuggestions` span only mutating
tools and were recorded unresolved-out-of-set. Pass 2 ran live on the four in-set candidates:
`url` (3 sites on `scrape`), `proxy` (basic/auto/enhanced), `sitemap` (include/skip/only on
`map`), and `paperId` (3 ids on `inspect_paper`). Every comparison came back **identical —
inconclusive, not disproven**; `sitemap=only` returned an empty list for example.com, a data
artifact rather than a shape difference.

The real polymorphism was one the advisory could never flag, because it is an **array** param:
`firecrawl_search`'s `sources`. `sources=web` returns `data.web[{url,title,description,position}]`;
`sources=news` returns `data.news[{title,url,snippet,date,imageUrl,position}]`. Unwrapping to
`data.web` would silently return `[]` for a news search, so `search` was typed as a generic
envelope base model (`SearchResult`: `success`, `creditsUsed`, `id`) with `data` left unmodelled.

## Shape decisions

- `scrape` → `ScrapeResult`, unwrap `[]`, only `markdown`. `metadata` is an open per-page bag
  (og:*, robots, generator differ per site) and was left unmodelled.
- `map` → unwrap `["links"]`, `list[MapLink]{url,title}` — the one clean list endpoint.
- `parse` → **surprise**: a local `filePath` returns a presigned-upload handshake
  (`mode`, `upload.uploadRef`, `nextToolCall`), not parsed content. The parsed-content mode needs
  a raw HTTP upload, which the skill forbids, so only the fields common to both modes are typed.
  A `.md` file is rejected outright as an unsupported upload type.
- `monitor_list` → `MonitorListResult{success, data: list}`; the element is unobservable on an
  empty account.
- Six research/developer search tools return **Markdown prose, not JSON**. Raw payloads were
  captured and checked for double-encoding: `NOT_JSON` in every case, so `-> Any`/`str` is the
  honest answer, not a probe failure.

The regenerated module parses cleanly (`ast.parse` OK) and five tools now return TypedDicts.
`run.py` is the harness verify stage's job and was not generated here.
