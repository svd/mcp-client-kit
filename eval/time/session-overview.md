# time — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:38Z
- **Duration:** 8m 48s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list time --schema` reported **2 tools**, both probed, none skipped:

- `get_current_time` — Get current time in a specific timezone
- `convert_time` — Convert time between timezones

Both carry explicit `annotations.readOnlyHint: true` (plus `destructiveHint: false`,
`idempotentHint: true`), so the mutating-tool classification never needed the keyword or
semantic fallback and nothing was withheld from the probe set. No seed commands were
configured, and none were needed — the server computes from the system clock and the IANA
database rather than from a store.

**Discriminators: N/A.** The `list --schema` stderr carried no advisory, and none could:
`convert_time` declares `source_timezone`, `time`, `target_timezone` while `get_current_time`
declares only `timezone`, so no parameter name is shared by two tools. Pass 2 was skipped
outright.

## Probing

Two probe invocations, batched in one shell call (local stdio needs no pacing).
`get_current_time` was multi-probed with `America/New_York` and `Europe/London` to widen
nullability and catch any DST-dependent field variation; `convert_time` was probed once with
`America/New_York` → `Asia/Tokyo` at `14:30`.

No surprises. Both tools returned a plain JSON object with no vendor envelope, no
double-encoding, and no error payloads — the rarer, easier case. The two `get_current_time`
probes deep-merged to an identical shape, so no field widened to `| None` or `Any`.

## Shape decisions

- **`get_current_time` → `CurrentTime`.** `unwrap: []` — the record *is* the top-level
  response. All four observed keys are top-level scalars, so they went in whole:
  `timezone: str`, `datetime: str`, `day_of_week: str`, `is_dst: bool`.
- **`convert_time` → `TimeConversion`.** `unwrap: []` for the same reason. Only
  `time_difference` is a top-level scalar; `source` and `target` are one-level-deep nests
  repeating the `CurrentTime` field set. Per the depth guard they are typed as `dict` rather
  than promoted into their own model — a single probe is not enough evidence to state the
  inner shape authoritatively, and typing the key as `dict` records that it exists without
  claiming what is under it.

No PII scrubbing was required: `probed_args` holds only IANA timezone names and a literal
`14:30`, all functional values the roundtrip verifier needs to replay.

## Verification

Regenerated with the sidecar in place; `ast.parse` succeeded. Both wrappers now return their
`TypedDict` (`-> TimeConversion`, `-> CurrentTime`) with zero `-> Any` returns remaining.
Runner generation was left to the harness verify stage.
