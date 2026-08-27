# everything — session overview

## Run Metadata

- **Executed:** 2026-08-27T11:03:12Z
- **Duration:** 4m 12s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list everything --schema` returned **13 tools**. The server supplies `annotations` on
every one, so classification was a clean `readOnlyHint` read with no keyword fallback needed.
Nine tools carried `readOnlyHint: true` and were selected; four carried `readOnlyHint: false`
and were skipped as mutating without being probed: `gzip-file-as-resource`,
`toggle-simulated-logging`, `toggle-subscriber-updates`, `simulate-research-query`. Transport is
local stdio, so the full non-mutating set was kept rather than pruned to record-carriers, and
probes were batched in one shell invocation with no pacing. No seed commands were configured.

## Discriminators

The `list --schema` stderr carried no advisory, which the precondition confirms: no parameter
name is shared by two or more tools, so nothing could clear the four-part test. The description
sweep surfaced two single-tool candidates the advisory cannot see by construction —
`get-annotated-message.messageType` (`error|success|debug`) and `get-resource-reference.resourceType`
(`Text|Blob`). Both were resolved by probing every enum value in separate invocations, reading
the part file between calls. All three `messageType` values returned prose `str` of differing
length but identical shape; both `resourceType` values returned the same three-element list.
Identical shapes are inconclusive, not disproven, so both were resolved by option 3
(unwrap-only `Any`) rather than promoted to a variant model.

## Shape decisions

Only **one** of the nine probed tools carries a modellable record. `get-structured-content` was
union-probed across all three `location` values in a single multi-`--args` probe and returned a
stable flat object → `unwrap: []` (the payload *is* the record, so no key path was invented),
`return_model: WeatherReport`, `fields: temperature int, conditions str, humidity int`.

The other eight stay `Any`, each for an observed reason:

- `echo`, `get-sum`, `trigger-long-running-operation`, `get-annotated-message` — genuine prose.
  Raw payloads were captured and the guarded JSON-in-string test reported `NOT_JSON` for all
  four, so `"str"` is a real text return, not an unparsed envelope.
- `get-resource-links`, `get-resource-reference`, `get-tiny-image` — mixed lists whose non-string
  elements are `resource` / `resource_link` / `image` metadata blocks. The bytes are dropped by
  design, so the probe never saw a record; element 0 is prose, which rules out `list[Link]`.
- `get-env` — returns the launched process's own environment. The 33 observed keys are
  machine-specific npm invocation state, not an API contract; typing them would state an
  authoritative lie and leak host detail into a committed artifact.

## Verification

Regeneration re-read the sidecar (`shapes: 9 tool(s)`), emitted `class WeatherReport` and
`get_structured_content(...) -> WeatherReport`, and the module parsed cleanly under `ast.parse`.
