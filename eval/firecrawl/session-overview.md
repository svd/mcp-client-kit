# firecrawl — session overview

## Run Metadata

- **Executed:** 2026-08-27T09:55:07Z
- **Duration:** 6m 43s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list` reports **26 tools**. `annotations.readOnlyHint` is supplied on every one, so
classification needed no keyword fallback: 16 read-only, 10 mutating (`firecrawl_crawl`,
`firecrawl_agent`, `firecrawl_interact`, `firecrawl_interact_stop`, `firecrawl_feedback`,
`firecrawl_search_feedback`, `firecrawl_monitor_create/update/delete/run`). No seed commands.

Because this is a hosted HTTP endpoint, the read-only set was pruned to the record-carrying
tools: **11 probed**. Five read-only tools went unprobed: `firecrawl_check_crawl_status` and
`firecrawl_agent_status` need a job id that only a mutating tool can mint, and
`firecrawl_monitor_get` / `_checks` / `_check` need a monitor id — `firecrawl_monitor_list`
returned `data: []`, so the account owns none.

## Discriminators

The `list` advisory named eleven shared scalars; all of them are object identity (`id`,
`paperId`, `scrapeId`, `url`), a free-text prompt, a cache/proxy knob, or a result count —
none switches shape. The two **real** discriminators are array-typed and therefore invisible
to the advisory; the description sweep found both:

- `firecrawl_scrape.formats` — the response gains one key per requested format. Probed default
  and `[markdown, html, links, summary]`; unioned into a `total=False` base model.
- `firecrawl_search.sources` / `categories` — four separate paced probes: default → `data.web`;
  `categories:[developer]` → `data.web` with an extra `category` key (the tool description
  claims results arrive in `data.developer` — the server does not do that); `sources:[news]` →
  `data.news`; `sources:[images]` → `data.images`. Confirmed, resolved as a generic base model.

## Shape decisions

| Tool | Unwrap | Model | Why |
|---|---|---|---|
| `firecrawl_map` | `links` | `list[MapLink]` | Clean vendor envelope; `{url, title}` records. |
| `firecrawl_search` | `data` | `SearchResults` | Buckets present-or-absent per variant; item models differ, so items stay untyped. |
| `firecrawl_scrape` | — | `ScrapeDocument` | No envelope. `links` (list) and nested `metadata` excluded per the top-level-scalars rule. |
| `firecrawl_monitor_list` | — | `MonitorListResult` | Empty store: `data` kept as the hand-added `list` marker, elements unobservable. |
| `firecrawl_parse` | — | `ParseResult` | Two-phase on hosted MCP. |
| 6 research/developer tools | — | `Any` | Genuine markdown prose. |

Surprises: `firecrawl_parse` on the hosted endpoint is two-phase — `filePath` returns a signed
GCS upload policy plus a `nextToolCall`, and only a follow-up `uploadRef` call returns parsed
content. Phase two was not observed (completing the upload needs a raw HTTP POST, which the
skill guards forbid), so only the field common to both phases (`success`) is modelled rather
than typing the phase-one envelope as if it were the result. Six tools return markdown, not
JSON — two were confirmed with `mcpgen call --out` (`NOT_JSON`), so `-> Any` is honest, not a
coverage gap. `firecrawl_research_read_paper` answered with the prose sentinel
`(no full-text passages available for this paper)`: a valid result for an unindexed paper.

The regenerated module parses cleanly (`ast.parse` OK, 26 wrappers, 5 `TypedDict`s);
`firecrawl_map` digs via `_dig_list`, `firecrawl_search` via `_dig`.
