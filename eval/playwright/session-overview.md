# playwright — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:14:59Z
- **Duration:** 6m 44s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list` reported **42 tools**. `annotations.readOnlyHint` is supplied on every one, so
classification needed no keyword fallback: **18 read-only, 24 mutating**. The 24 mutating tools
(navigate, click, type, fill_form, drag, mouse_*, evaluate, run_code_unsafe, tabs, close,
handle_dialog, file_upload, resize, resume, …) were skipped without probing.

Of the 18 cleared tools, **17 were probed** and one was dropped: `browser_annotate` opens the
Playwright Dashboard and *waits for a human to draw*, so probing it would hang a headless run.
No seed commands are configured; the `--init-page servers/playwright-init.ts` hook puts each
freshly spawned browser on `https://playwright.dev/` before any tool dispatches.

## Discriminators

The `list` advisory raised ten candidates. `button`, `url`, `x`, `y` span only mutating tools and
are recorded unresolved. `index`, `duration`, `text`, `element` fail Pass 1 as window / query /
description params. Two survived and were resolved by live Pass 2 probes:

- **`filename`** (console_messages, network_requests, network_request, snapshot, pdf_save, …) —
  descriptions promise "returned as text" vs. saved-to-file, a genuine behavioural switch. Probed
  with and without on `browser_console_messages`: both `str`. Content differs, shape does not.
- **`part`** on `browser_network_request` — a single-tool, 4-value enum invisible to the advisory,
  caught by the description sweep. All four values plus the omitted case were probed: all five
  returned `str`.

`level` (4 values, two probed) likewise moved content only.

## Shape decisions

**Every one of the 17 probed tools returned `str`.** Raw payloads were captured via
`mcpgen call --out` for 14 of them and inspected directly: the server speaks markdown, not JSON —
`### Result` / `### Error` sections, an accessibility tree rendered as indented YAML-ish prose,
a numbered network list. The guarded JSON-in-string test does not apply; nothing double-encodes.
So `unwrap` stays empty, `return_model` stays `null`, and all 42 wrappers keep `-> Any`. This is
`no_shaped_tool_by_design`, not a coverage gap.

`browser_take_screenshot` is the one non-`str`: a 2-block list of markdown text plus an image
block whose bytes the probe deliberately drops (`{type, mimeType, has_data}`). Per the media rule
it is left `Any` with no modelled payload.

Five tools were marked `"_probe_status": "inconclusive"` — the shape was never observed because
every response was an error. `browser_stop_tracing` answered *"Tracing is not started"* and
`browser_video_chapter` / `browser_video_show_actions` / `browser_video_hide_actions` /
`browser_wait_for` all answered *"No open pages available."* The latter is the surprise of the
run: `browser_snapshot` (14.5 KB) and `browser_find` (33 matches) clearly saw the live page in the
same configuration, so these four appear to read the current tab without triggering the lazy page
creation that `--init-page` hangs off. `browser_stop_video` returned *"No videos were recorded."* —
a real result, not an error, since each probe is a fresh process.

The regenerated module parses cleanly (`ast.parse`, 47,223 bytes, 42 typed stubs).
