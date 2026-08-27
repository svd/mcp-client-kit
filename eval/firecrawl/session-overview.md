# firecrawl — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:13:02Z
- **Duration:** 7m 31s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **26 tools**. `annotations.readOnlyHint` is supplied on every one, so
classification needed no keyword or semantic fallback: **16 read-only, 10 mutating**
(`firecrawl_crawl`, `firecrawl_agent`, `firecrawl_interact`, `firecrawl_interact_stop`,
`firecrawl_feedback`, `firecrawl_search_feedback`, `firecrawl_monitor_create/update/delete/run`).
All 10 were skipped unprobed.

Because this is a hosted HTTP endpoint, the read-only set was pruned to the record-carrying
tools and probed serially with a 2 s pause. **10 tools were probed.** Six read-only tools were
left unprobed for want of a real object to name: `firecrawl_check_crawl_status` and
`firecrawl_agent_status` take ids minted only by the mutating `crawl`/`agent` tools;
`firecrawl_monitor_get`, `_checks`, and `_check` need a monitor id, and `firecrawl_monitor_list`
returned an empty account; `firecrawl_parse` reads a server-side `filePath` that a hosted
endpoint cannot see. Inventing ids against a validating server would have recorded an error
shape, so none was invented.

## Surprises

- Six tools return **Markdown prose, not JSON**. The guarded `json.loads` test on the captured
  raw payload returned `NOT_JSON`, so `"_observed_shape": "str"` stands as an honest verdict
  rather than a probe failure.
- `firecrawl_search`'s `sources` is an array of **objects**, not strings. A probe with
  `["news"]` failed schema validation *silently* — the part file kept the previous variant, so
  the shapes looked unchanged. Only a raw `call` surfaced the `-32602` validation error.
- `firecrawl_research_related_papers` answered `(no results) (poolSize=0)` for one seed; it was
  re-probed with a canonical seed so `probed_args` replay to real records.

## Shape decisions

- **`firecrawl_map`** → `unwrap: ["links"]`, `list[MapLink]` (`url`, `title`). The only clean
  envelope on the server.
- **`firecrawl_scrape`** → `ScrapeResult`, unwrap-free. `formats` is a **confirmed array
  discriminator**: `["markdown"]` yields `{markdown, metadata}`, `["links","summary"]` yields
  `{links, summary, metadata}`. Array-typed params rule out `Literal` overloads, so this is
  step-4 option 2 — a `total=False` union of both probes. `metadata` stays `dict`; its keys are
  page-controlled OG/Twitter tags.
- **`firecrawl_search`** → `SearchResponse`, unwrap-free. `sources` switches both the key under
  `data` and its element shape (`web`/`news`/`images`, all three observed). No single unwrap
  path reaches the records under all three, so `data` stays `dict` and the envelope is kept —
  which also preserves `id`, the searchId `firecrawl_search_feedback` consumes.
- **`firecrawl_monitor_list`** → `MonitorListResponse` with a hand-added `data: list`; the
  element model is unobservable against an empty account.
- The six prose tools stay `-> Any` with `return_model: null`.

`mcpgen codegen` re-ran against the edited sidecar and the module **parsed cleanly**
(`ast.parse`, 58 042 bytes), emitting four `TypedDict`s and one `_dig_list` unwrap.
