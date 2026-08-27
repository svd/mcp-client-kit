# firecrawl — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-26T20:03:31Z
- **Duration:** 7m 5s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list firecrawl --schema` returned **26 tools**. Every tool carried
`annotations.readOnlyHint`, so selection needed no keyword heuristic: **16 read-only tools were
probed** and **10 mutating tools were skipped** — `crawl`, `agent`, `interact`, `interact_stop`,
`feedback`, `search_feedback`, and the four `monitor_create/update/delete/run` writers.

`list` flagged eleven discriminator candidates; all failed Pass 1. `k` and `maxAge` are window
params, `url`/`id`/`paperId`/`scrapeId` are unbounded identity args, and `prompt`, `proxy`,
`sitemap`, `querySuggestions`, `rating` are input-only config absent from every response. No
`discriminator` block was emitted and no single-variant model was minted.

## Surprises

- **`firecrawl_parse` is a two-phase upload flow.** Probed with a local `filePath`, it does not
  return parsed markdown — it returns a signed-upload envelope (`upload.uploadUrl`, `uploadRef`,
  a `nextToolCall` telling the caller to re-invoke `parse` with `uploadRef`). Completing phase two
  needs a raw HTTP PUT, which the guards forbid, so the `uploadRef` branch was never observed.
- **Six research/dev tools return prose, not JSON.** `developer_search` and the five
  `research_*` readers all returned markdown. Each was re-fetched raw and tested for
  JSON-in-string; none parsed. `_observed_shape: "str"` is honest — successful text, not errors.
- **The account holds zero monitors.** `firecrawl_monitor_list` returned `{"success": true,
  "data": []}` — a genuine success payload with an unobservable element shape.
- **Five status tools could not be observed.** They need ids that only the skipped mutating
  tools mint (`crawl`, `agent`, `monitor_create`). Probed with a synthetic UUID they returned
  404 / "Monitor not found" / "Check not found" — error strings, not shapes — so each carries
  `"_probe_status": "inconclusive"`.

## Shape decisions

| Tool | Unwrap | Model | Why |
|---|---|---|---|
| `firecrawl_scrape` | — | `ScrapeResult` | Flat record, no vendor envelope; `metadata` stays `dict` (one probe, format-dependent keys). |
| `firecrawl_map` | `links` | `list[MapLink]` | Envelope is a single `links` key over uniform `{url, title}` records. |
| `firecrawl_search` | — | `SearchResponse` | `data` is keyed by source (`web`/`news`/`images`) and switches on the `sources` input, so digging to `data.web` would lie for other sources; `data` stays `dict` while `success`/`creditsUsed`/`id` are typed. |
| `firecrawl_parse` | — | `ParseResponse` | Only the upload-instruction branch was observed; `total=False` keeps the unseen parsed branch from being contradicted. |
| `firecrawl_monitor_list` | `data` | none (`Any`) | Envelope stripped, but zero elements were seen — fabricating an element schema from an empty list was refused. Re-probe after creating a monitor to type it. |
| 6 prose tools | — | none | Markdown text; a `TypedDict` would be a fiction. |
| 5 status tools | — | none, `inconclusive` | Shape never observed. |

## Result

The regenerated module **parses cleanly** (`ast.parse` OK). Four `TypedDict`s are emitted, the
shaped signatures read `-> ScrapeResult`, `-> list[MapLink]`, `-> SearchResponse` and
`-> ParseResponse`, and their bodies dig via `_dig` / `_dig_list`. The `firecrawl_parse`
`probed_args` held a machine-local path, scrubbed to `<example-file-path>.html`; the gitignored
`firecrawl.verify.json` keeps the real value for the roundtrip verifier.
