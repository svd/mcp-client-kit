# time — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:29Z
- **Duration:** 2m 56s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Setup

Resolved CLI: `uv run mcpgen` (0.9.0.dev1) — a bare `mcpgen` is absent in this
uv-managed project. Every command carried `MCPGEN_SERVERS=.mcp.eval.json`. The folder already held
artifacts from an earlier run, and `codegen` auto-detects a sidecar beside its `--out`;
leaving them would have pre-shaped the module before the first probe. It was cleared and
stubs regenerated from a live `tools/list`, `return_model: null` throughout.

## Tools

The server exposes **2 tools**, and **both were probed** — nothing skipped, no seeds.

```
Tools on time:
  get_current_time — Get current time in a specific timezone
  convert_time     — Convert time between timezones
```

Both carry `annotations.readOnlyHint: true` alongside `destructiveHint: false` and
`idempotentHint: true`, so the annotations do not contradict themselves. Neither name
matches the mutating keyword test (`convert` is not a mutating verb), so the hint is
undisputed on both and the keyword fallback never ran.

**Discriminators: N/A.** The `list --schema` advisory on stderr was empty, and the
precondition confirms why: the two tools share no parameter name at all
(`timezone` vs. `source_timezone` / `time` / `target_timezone`), so no candidate can
exist. Pass 2 was skipped outright.

## Probing

`get_current_time` was multi-probed with `UTC` and `America/New_York` — one non-DST and
one DST zone — to test whether `is_dst` widens or fields drop out. It did not: both
returned the identical four-key shape, which is the useful negative result. `convert_time`
took a single probe (`UTC` → `Asia/Tokyo` at `14:30`).

No surprises: no vendor envelope, no double-encoded JSON string, no empty lists, no
errors. Responses were 116 and 267 bytes — this server returns records, not prose. All
`probed_args` are IANA timezone names and a wall-clock string; none matches a PII
pattern, so the scrub pass changed nothing.

## Shape decisions

- **`get_current_time` → `CurrentTime`, `unwrap: []`.** The record is the top-level
  object; there is nothing to strip. Fields are four observed top-level scalars —
  `timezone`, `datetime`, `day_of_week` (`str`), `is_dst` (`bool`). Nothing observed as
  `None` across the two probes, so no field is nullable.
- **`convert_time` → `TimeConversion`, `unwrap: []`.** Tempting to unwrap to `source` or
  `target`, but both are half the answer and `time_difference` sits beside them — digging
  either would discard data the caller asked for. `unwrap` stays empty. `time_difference`
  is the one top-level scalar; `source` and `target` are typed `dict` rather than modelled,
  per the guard that deeper nests stay `dict`. Their inner shape matches
  `CurrentTime`, but the shape-spec cannot name an inner model.
- Names are distinct and the `fields` dicts differ, so no `return_model` collision.
- Neither tool is a mutating suspect, so no `_mutating_suspect` markers were added.

## Verification

Regenerated with the sidecar in place; `ast.parse` succeeded. `convert_time` now reads
`-> TimeConversion` and `get_current_time` `-> CurrentTime`, both `TypedDict` bodies
emitted with `total=False`. No `Any` returns remain.
