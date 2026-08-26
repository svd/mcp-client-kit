# firecrawl — session overview

## Run Metadata

- **Executed:** 2026-08-26T14:48:04Z
- **Duration:** 8m 59s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

The bearer-authenticated endpoint exposes **26 tools**. `annotations.readOnlyHint` is
populated on every one, so tool selection came straight from the primary signal — no
keyword heuristic needed. **16 read-only tools were probed; 10 mutating tools were
skipped** (`crawl`, `agent`, `interact`, `interact_stop`, `feedback`, `search_feedback`,
`monitor_create`/`update`/`delete`/`run`). No seed commands were configured or run.

`mcpgen list` flagged 11 discriminator candidates. All were disqualified: `k`, `maxAge`
and `limit` are window/count args; `id`, `paperId`, `scrapeId` and `url` are identity
args; `prompt` is free text; `sitemap` and `proxy` are transport options; `rating` and
`querySuggestions` belong to skipped mutating tools. None appears as a key in any
observed response.

## Surprises

- **`firecrawl_parse` does not parse.** Given a `filePath` it returns a presigned-upload
  ticket (`upload.uploadUrl`, `uploadRef`, `nextToolCall`) — the actual parse is a second
  call keyed on `uploadRef`. That second-phase shape is unobservable without an out-of-band
  GCS upload, so only the ticket is modelled. A first probe with a `.md` file was rejected
  ("Unsupported upload type"); re-probed with an HTML fixture.
- **Frequent transient 503s** from `https://mcp.firecrawl.dev/v2/mcp`. Roughly a third of
  calls failed and succeeded on retry; probes were wrapped in a bounded 503-only backoff.
- **The account holds no monitors** (`monitor_list` → `data: []`), so the inner monitor
  record is unobservable and `data` stays `list`.

## Shape decisions

| Tool | unwrap | model |
|---|---|---|
| `firecrawl_scrape` | `[]` | `ScrapeResult` — flat record; format keys unioned across two probes (`markdown` / `html`+`links`+`summary`) |
| `firecrawl_map` | `["links"]` | `list[MapLink]` (`url`, `title`) |
| `firecrawl_search` | `["data"]` | `SearchResults` — `sources[].type` picks which of `web`/`news`/`images` appears, so three probes were unioned into one `total=False` base model rather than a variant overload (nested array-of-objects can't drive codegen overloads) |
| `firecrawl_monitor_list` | `[]` | `MonitorList` |
| `firecrawl_parse` | `[]` | `ParseUploadTicket` |

**Six research/developer search tools** (`developer_search`, `research_search_papers`,
`research_search_github`, `research_inspect_paper`, `research_read_paper`,
`research_related_papers`) genuinely return markdown prose. Raw payloads were read to
confirm these are content, not errors — left `-> Any` honestly.

**Five id-gated tools** (`check_crawl_status`, `agent_status`, `monitor_get`,
`monitor_checks`, `monitor_check`) had no valid id available: crawl and agent jobs only
exist after a mutating start, and the monitor list is empty. Probing with a synthetic
UUID returned "not found" error strings, so each is marked `_probe_status: "inconclusive"`
rather than passed off as a text-returning tool.

The regenerated module parses (`ast.parse`, 845 lines), imports cleanly, and exports 26
wrappers with 5 `TypedDict`s. Enum params rendered as `Literal[...]` automatically.
`probed_args` scrubbed: the `parse` fixture path became `<example-local-file>.html`.
