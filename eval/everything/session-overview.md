# everything — Session Overview

## Run Metadata

- **Executed:** 2026-08-25T15:42:09Z
- **Duration:** 1m 1s

## What happened

The `everything` reference server exposes 13 tools. `mcpgen list --schema` classified
3 as mutating by `annotations.readOnlyHint: false` (`gzip-file-as-resource`,
`toggle-simulated-logging`, `toggle-subscriber-updates`) and 10 as read-only. No
discriminator-candidate advisory was raised across the tool set — none of the shared
params (`resourceType`, `outputType`, etc.) recur across enough tools to qualify.

This run reused a previously probed, already-scrubbed `everything.shapes.json` sidecar
(all 13 entries carry `"source": "live"` and clean `probed_args`, with no PII present)
rather than re-issuing live probe calls, then regenerated the module from it and
re-verified end to end.

Only one tool produced a genuinely typeable shape: **`get-structured-content`**, which
returns a flat dict (`temperature: int`, `conditions: str`, `humidity: int`) for a
`location` enum probe of `"New York"`. It was shaped as `WeatherConditions` with
`unwrap: []` (no envelope) and `return_container` omitted (single record, not a list).

The remaining 12 tools stayed `-> Any`, each for a distinct, well-documented reason
captured in the shape-spec's `_note` fields:
- `get-env` — returns a live-machine env-var dict whose keys aren't stable across
  environments; typing it would overfit to one probe.
- `get-resource-links`, `get-resource-reference`, `get-tiny-image`,
  `gzip-file-as-resource` — return MCP resource-link / embedded-resource / image
  content blocks; `McpCaller` only surfaces the accompanying text summary, so the
  structured payload (uri, mimeType, blob/text, base64 image data) is invisible to a
  text-only probe.
- `echo`, `get-annotated-message`, `get-sum`, `simulate-research-query`,
  `trigger-long-running-operation` — return plain prose/scalar text with no stable
  structured shape to model.
- `toggle-simulated-logging`, `toggle-subscriber-updates` — mutating toggles with
  trivial ack responses; not worth a `TypedDict`.

The regenerated `everything.py` (10,071 bytes) parses cleanly under `ast.parse`. All
five `eval-kit verify` checks passed: `ast`, `signatures` (the shaped tool's signature
reads `-> WeatherConditions`, not `Any`), `idempotency` (deterministic `render_module()`
against stub schemas), `pii` (no PII detected in `probed_args`), and `roundtrip` (a
live call to `get-structured-content` returned a typed, well-formed result).

No inconclusive/quota-error entries were needed — every probed tool returned a normal
success payload.
