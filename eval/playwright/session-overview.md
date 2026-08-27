# playwright — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:10:09Z
- **Duration:** 8m 53s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

`mcpgen list playwright --schema` returned **42 tools**. This server sets
`annotations.readOnlyHint` on every one of them, so step 2b's primary rule decided the
whole selection with no keyword guessing: **18 read-only**, **24 mutating** (skipped).
No hint was disputed — every `readOnlyHint: true` tool also carried
`destructiveHint: false`, and none of their names passes the keyword test (`save`,
`hide`, `show`, `start`, `stop` are not mutating verbs on that list).

**17 of the 18 were probed.** `browser_annotate` was not: it opens the Playwright
Dashboard and blocks until a human draws annotations. A bounded probe hung past 120 s
with no part file written, and was killed. Recorded as unprobed — interactive by design,
unreachable from a headless run.

## Seeds

None. Instead, `--init-page servers/playwright-init.ts` puts each freshly-spawned probe
process on `playwright.dev` and emits one console message per level, so read tools have
real content instead of `about:blank`.

## Surprises

Every probe spawns a new process, and the browser lives inside it, so nothing survives a
call. That split the 17 cleanly:

- **12 returned a real result.** All are markdown prose — `### Result` / `### Page` /
  `### Ran Playwright code` blocks — with no JSON envelope. The JSON-in-string test on
  the captured raw payloads came back `NOT_JSON` for every one.
- **5 returned only an error, never a result.** `browser_stop_tracing` →
  `Tracing is not started`; `browser_video_chapter`, `browser_video_show_actions`,
  `browser_video_hide_actions`, `browser_wait_for` → `No open pages available`. The last
  four need a page the init hook only creates for tools that open a tab on demand. These
  carry `"_probe_status": "inconclusive"` — the shape was never observed, and a bare
  `"str"` would be indistinguishable from a genuine text tool.

`browser_take_screenshot` was the only non-scalar shape: a two-block list of a text block
plus an image block that collapses to `{type, mimeType, has_data}`. The bytes are
deliberately dropped, so the observed shape describes the envelope, not a record.

## Shape decisions

**No tool was shaped.** Every entry keeps `unwrap: []` and `return_model: null`, so all
42 wrappers stay `-> Any`. That is the honest reading, not a coverage gap: this server
returns prose for humans, and a `TypedDict` over `str` would be a fabricated claim.
`browser_take_screenshot` stays `Any` under the media rule.

## Discriminators

Ten candidates were advised; `button`, `url`, `x`, `y` span only mutating tools and stay
unresolved outside the selected set. Pass 2 probed `target` (`body` / `nav` / omitted)
and `filename` on `browser_snapshot`, `text` (`Playwright` / `Docs` / a no-match string)
on `browser_find`, `index` (1 / 2 / 3) on `browser_network_request`, and `element` on
`browser_highlight`. Every variant observed `"str"` — **inconclusive, not disproven**.
Resolved as step 4 option 3 (unwrap-only `Any`), which is what a prose return already is.

## Verification

`ast.parse` clean: 42 async defs, 0 `TypedDict`s, 12 params rendered as `Literal[...]`
from their declared enums.
