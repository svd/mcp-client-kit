# exa — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:02:10Z
- **Duration:** 2m 48s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list exa --schema` returned **2 tools**, both probed, none skipped:

```
Tools on exa:
  web_search_exa  — Search the web for any topic and get clean, ready-to-use content
  web_fetch_exa   — Read a webpage's full content as clean markdown
```

Both carry `annotations.readOnlyHint: true` alongside `destructiveHint: false` and
`idempotentHint: true` — the hint contradicts neither itself nor the name, since neither
name passes the keyword test (`web`, `search`, `fetch`, `exa` are all reads). The hints stand
untouched, so there is no `## mutating-skipped` section: nothing was skipped.

**discriminators: N/A.** No parameter name is shared by both tools — `query`/`numResults`
belong to search, `urls`/`maxCharacters` to fetch — so the step-2.e precondition (two or
more tools declaring the same scalar param) is never met and the advisory could not fire.

## Probe results and the surprise

Both probes were paced ≥2 s apart against the hosted endpoint. Both succeeded, and both
observed `_observed_shape: "str"` — 25,084 bytes for `web_search_exa` (3 results), 1,601
bytes for `web_fetch_exa`.

Per the JSON-in-string rule I captured both raw payloads with `mcpgen call --out` and ran
the guarded `json.loads` test. Both returned **`NOT_JSON`**: the payloads are genuine
prose. `web_search_exa` emits a human-readable digest — `Title:` / `URL:` / `Published:` /
`Author:` / `Highlights:` blocks separated by `...`, with markdown bodies inline.
`web_fetch_exa` emits the page as plain markdown under an `# <slug>` heading. Reading the
content confirmed these are successful results, not error text — so neither entry gets
`_probe_status: "inconclusive"`, which would have misreported a working server as
unobservable.

## Shape decisions

Neither tool is shapeable, and that is the honest answer rather than a coverage gap:

- **`web_search_exa`** — `unwrap: []`, `return_model: null`. There is no vendor envelope
  and no record; the wrapper's `-> Any` correctly describes a returned `str`. Setting a
  `TypedDict` here would claim a dict the tool never returns.
- **`web_fetch_exa`** — identical reasoning, identical outcome.

The one judgment applied is `input_overrides`. JSON Schema types `numResults` and
`maxCharacters` as `number`, which codegen renders `float`, but both are counts
(`maxCharacters` even declares `minimum: 1`) and both were accepted as ints live. Both are
overridden to `int`, so the regenerated signatures read `numResults: int | None` and
`maxCharacters: int | None`.

Nothing in `probed_args` matched a PII pattern — a public docs URL, a generic query, two
integers — so the scrub pass changed nothing.

The regenerated module **parsed cleanly** (`ast.parse` OK). Runner generation was left to
the harness verify stage, per the eval contract.
