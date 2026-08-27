# Playwright MCP — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:50:17Z
- **Duration:** 8m 37s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list` reported **42 tools**. Annotations were complete and trustworthy, so step 2b
classified entirely on `readOnlyHint`: 24 tools carry `readOnlyHint: false` with
`destructiveHint: true` (navigation, clicks, typing, `browser_evaluate`,
`browser_run_code_unsafe`, tab management) and were skipped without probing. Of the 18
read-only tools, **17 were probed**. The one exclusion is `browser_annotate`, whose own
description says it "waits for the user to draw annotations" — a headless, non-interactive
run has nobody to draw, so probing it would have blocked the sweep rather than returned a
shape.

No seed commands are configured. Page state comes from `--init-page servers/playwright-init.ts`,
which sizes the viewport, navigates to `playwright.dev`, and emits one console message per
level. Every `mcpgen probe` spawns a fresh server process, so without it each probe would have
observed `about:blank`.

## Discriminators

The `list --schema` advisory raised ten candidates. Four (`button`, `url`, `x`, `y`) span only
mutating tools and are recorded unresolved. The six reaching the selected set — `duration`,
`element`, `filename`, `index`, `target`, `text` — each got Pass 2: three distinct values on
one read-only tool, everything else held fixed. All eighteen probes returned identical shapes.
That is **inconclusive, not disproven**: the six stay polymorphic-suspect, and step 4 resolved
them by option 3 (unwrap-only `Any`), which is where the shape evidence pointed anyway.

## Shape decisions

**Every probed tool returned `str`.** Playwright MCP speaks human-readable markdown, not
records: an `### Result` heading, an optional fenced `js` block echoing the Playwright code it
ran, then `### Page` and `### Events` sections. The accessibility snapshot is fenced YAML
inside that markdown. JSON-in-string detection was run against raw payloads for
`browser_snapshot` and `browser_network_request` — both `NOT_JSON`. So for all 17 entries:
`unwrap: []`, `return_model: null`, `fields: {}`. No `TypedDict` is emitted, and none should
be — inventing one would parse markdown the wrapper never returns.

`browser_take_screenshot` is the only tool whose shape is not a bare `str`: a two-element
content list, a text block plus an image block whose base64 bytes mcpgen deliberately drops.
That describes the MCP envelope, not a record, so it stays `Any` per the media-tool guard.

Five tools returned **only** an error and never a success payload —
`browser_wait_for`, `browser_video_chapter`, `browser_video_show_actions` and
`browser_video_hide_actions` with `Error: No open pages available.`, and
`browser_stop_tracing` with `Error: Tracing is not started`. These carry
`"_probe_status": "inconclusive"` so the verifier does not read them as genuine
text-returning tools.

Eleven probed tools are flagged `_mutating_suspect` despite `readOnlyHint: true`: they write
files into `--output-dir` (`browser_pdf_save`, `browser_take_screenshot`, the tracing and
video pairs) or mutate live page state (`browser_highlight`, `browser_hide_highlight`, the
action-overlay toggles). The flag records the discrepancy without overriding the server's own
declaration.

## Result

Regeneration consumed all 17 shape entries; the module parses (`ast.parse` clean, 42
functions). Nine `Literal[...]` unions came from `inputSchema` enums, so the input side is
genuinely typed even though the return side cannot be.
