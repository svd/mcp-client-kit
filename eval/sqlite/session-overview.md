# sqlite — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:28Z
- **Duration:** 2m 26s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Setup

Resolved CLI: `uv run mcpgen` (0.9.0.dev1) — clears both the 0.3.0 floor and the 0.7.0
runner floor. Transport: stdio via `MCPGEN_SERVERS=.mcp.eval.json`. No seed commands were
configured; the `/tmp/eval.db` store already held two tables (`users`, `products`, one row
each) left by an earlier run, so probing found real data without any mutating call.
A stale `sqlite.shapes.json` from that earlier run was deleted before step 1 so the stubs
were generated blind, as a fresh run requires.

## Tool selection

The server exposes **6 tools**, none carrying `annotations` — so the keyword + semantic
fallback decided every verdict.

- Probed (3): `read_query`, `list_tables`, `describe_table`
- Skipped (3): see `## mutating-skipped`

`discriminators: N/A`. The only cross-tool shared parameter is `query`
(`read_query`/`write_query`/`create_table`), which is on the engine's own denylist, so no
candidate could clear the precondition and no advisory fired. Step 2.e Pass 2 was skipped.

## mutating-skipped

- `write_query` — name contains `write`; no readOnlyHint
- `create_table` — name contains `create`; no readOnlyHint
- `append_insight` — name contains `append`; no readOnlyHint

## Observations

Every response is a bare JSON array at the top level — no vendor envelope anywhere on this
server, so `unwrap` is empty for all three tools and no `_dig_list` helper is emitted. That
is the honest result, not a missed unwrap.

`describe_table` was multi-probed against both tables. Its rows are `PRAGMA table_info`
output: `cid`, `name`, `type`, `notnull`, `dflt_value`, `pk`. `dflt_value` came back null in
all ten observed columns, so it merged to `Any | None` — the type is genuinely unobserved,
and widening it to `str | None` from zero non-null samples would have been a guess.
`notnull` and `pk` are SQLite integers, not booleans, and are typed as such.

## Shape decisions

- **`list_tables`** → `list[TableName]`, unwrap `[]`, fields `{name: str}`. Trivial and
  stable; the array is the record.
- **`describe_table`** → `list[ColumnInfo]`, unwrap `[]`, six fields as above. Distinct
  name from `TableName` despite both carrying `name`, since the field sets differ.
- **`read_query`** → deliberately left `-> Any`. Its row keys are the caller's own `SELECT`
  projection, not a server-fixed record: the probe observed
  `{id, name, email, active, score}` only because the query was `SELECT * FROM users`. Any
  `TypedDict` minted from that would misdescribe every other query. The observed shape is
  kept in the spec under `_observed_shape` with a `_shape_note` recording why it was not
  promoted to a model.

`probed_args` hold table names and a standard SQL statement — functional values, no PII, so
nothing was scrubbed.

## Verification

Regeneration succeeded; `ast.parse` clean. `describe_table` reads
`-> list[ColumnInfo]` and `list_tables` reads `-> list[TableName]`; the three mutating
tools remain untouched `-> Any` stubs. `sqlite.verify.json` carries args for
`describe_table` and `read_query` (`list_tables` takes none, so merge omits it).
