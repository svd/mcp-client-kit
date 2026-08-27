# firecrawl — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T06:07:44Z
- **Duration:** 15m 42s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

The server exposes **26 tools**, and every one carries `annotations.readOnlyHint`, so
selection took step 2b's primary path without falling back to the keyword heuristic.
That cleared **16 read-only tools** and skipped 10 mutating ones. No `readOnlyHint: true`
was disputed.

Of the 16 selected, **11 were probed** and 5 could not be. `check_crawl_status` and
`agent_status` need job ids mintable only by the mutating `crawl` / `agent` tools; the
three `monitor_*` readers need a monitor id, and `monitor_list` returned `data: []` — the
account holds none, and creating one is mutating. Coverage lost to the read-only
constraint, not to failure.

## mutating-skipped

`search_feedback`, `feedback`, `crawl`, `agent`, `interact`, `interact_stop`,
`monitor_create`, `monitor_update`, `monitor_delete`, `monitor_run` — all
`readOnlyHint: false`; the last three also `destructiveHint: true`.

## Discriminators

Eleven candidates were advised. Pass 1 dropped `k` and `maxAge` as window params;
`prompt`, `rating`, `querySuggestions` and `scrapeId` span only mutating tools
(recorded unresolved, never probed).

- **`sitemap` on `map` — CONFIRMED.** All three enum values probed: `include` and `skip`
  return links carrying `url`/`title`/`description`; `only` returns `url` alone. Resolved
  by step 4 option 1 — overloads on `Literal`. Consequence: `sitemap` becomes a **required**
  arg on the generated `firecrawl_map`.
- **`proxy` on `scrape` — inconclusive.** `basic`/`enhanced`/`auto` gave identical shapes.
- **`paperId` — inconclusive.** Two arXiv ids and one DOI all returned prose.
- **`url` on `scrape`** varies only inside `metadata`, an open-ended passthrough of page
  meta tags; the top level stayed stable. Held as `dict` rather than modelled.
- **`id`** could not be tested — no ids exist to compare.

## Shape decisions

- **`scrape` → `ScrapeResult`**, `unwrap: []`. There is no vendor envelope; the record is
  the top level. A `formats` probe widened it honestly to `markdown`/`html`/`summary`/
  `links: list[str]` plus the variadic `metadata: dict`. `formats` is an array param, so
  the advisory could not flag it — it is a real shape switch found by hand.
- **`search` → `SearchResults`**, `unwrap: ["data"]`. `sources` swaps `data`'s sub-key
  entirely (`web` / `news` / `images`, each a different element shape). An array-of-object
  param cannot drive codegen overloads, so this took option 2: a base model of three
  optional lists. Typing `list[WebResult]` off `data.web` would return `[]` for a news
  query — a silent lie.
- **`map` → `list[MapLink] | list[MapLinkUrlOnly]`** via `unwrap: ["links"]`.
- **`parse` → `ParseUploadTicket`**. A surprise: given `filePath` it returns a presigned
  upload ticket and a `nextToolCall`, not parsed content. The parsed branch is unobserved.
  A `.md` file was rejected first ("unsupported upload type"); `.csv` succeeded.
- **`monitor_list`**: envelope stripped to `data`, but the element is unobservable against
  an empty store, so `return_model` stays null and it returns `Any`.
- **Six research/developer tools return markdown prose**, verified against raw payloads —
  genuine text, not errors — so they honestly stay `-> Any`.

The regenerated module parses (`ast.parse`), imports cleanly, and its `TypedDict`s resolve.
