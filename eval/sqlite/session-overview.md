# sqlite — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:39Z
- **Duration:** 7m 59s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list sqlite --schema` returned **6 tools**. The server supplies no `annotations`, so
classification fell back to the keyword test plus a semantic read of each description:

- `read_query` — SELECT only, read-only
- `list_tables` — read-only
- `describe_table` — read-only
- `write_query` — **MUTATING** (INSERT/UPDATE/DELETE)
- `create_table` — **MUTATING** (DDL)
- `append_insight` — **MUTATING** ("Add … to the memo"; `append` is on the mutating keyword list)

Selected set: the three read-only tools; the three mutating tools were skipped entirely and
never probed. No seed commands were configured; `/tmp/eval.db` already held `users` and
`products` from an earlier run, so every read tool had real rows to return. Three selected
tools is under the fan-out threshold, so the run stayed single-driver.

**Discriminators: N/A.** `list --schema` emitted no advisory on stderr, and the precondition
confirms why: the only parameter shared by two or more tools is `query` (`read_query`,
`write_query`, `create_table`), which sits on the engine denylist. `table_name` and `insight`
each appear on a single tool. Pass 2 was therefore skipped.

## Probes and responses

Bootstrapping used `mcpgen call sqlite list_tables --out …probe-raw.json`, which returned
`[{"name": "users"}, {"name": "products"}]` — real table names to feed `describe_table`. Three
probes then went out batched in one local-stdio invocation (no pacing needed): `list_tables`
with `{}`, `describe_table` multi-probed against both `users` and `products`, and `read_query`
with `SELECT * FROM users LIMIT 3`.

The one surprise was a pleasant one: **this server wraps nothing.** All three payloads arrived
as bare top-level JSON lists of row dicts — no `data`/`results`/`content` envelope, and no
double-encoded JSON string. Every `unwrap` is therefore `[]`, and the generated bodies are
plain casts rather than `_dig_list` calls.

## Shape decisions

- **`list_tables` → `list[TableRef]`** (`name: str`). Unwrap `[]`; the response is already the
  record list. One field, fully observed.
- **`describe_table` → `list[ColumnInfo]`** (`cid: int`, `name: str`, `type: str`,
  `notnull: int`, `dflt_value: Any | None`, `pk: int`). Unwrap `[]`. This is `PRAGMA
  table_info` output, so the shape is stable across tables — the two-table multi-probe merged
  to an identical key set, which is the evidence for that claim. `dflt_value` was `None` in
  every observed column, so its non-null type is genuinely unknown and stays `Any | None`
  rather than being guessed as `str`.
- **`read_query` → `Any`** (deliberate, not a coverage gap). The probe did return a clean
  `list[{id: int, name: str, email: str, active: int, score: float}]`, but those columns are an
  artifact of the SQL the caller passed, not of the tool. Typing `read_query` as
  `list[UserRow]` would misdescribe every other SELECT, so `return_model` stays `null` with
  empty `fields`. This is the "don't state authoritative lies" guard applied to a tool whose
  response shape is caller-determined by construction.

`probed_args` needed no scrubbing: table names, an empty dict, and a plain SELECT are all
functional values, not PII.

## Verification

Regeneration picked up the sidecar (`shapes: … (3 tool(s))`) and `ast.parse` succeeded.
`describe_table` and `list_tables` now read `-> list[ColumnInfo]` / `-> list[TableRef]`; the
three mutating tools and `read_query` remain `-> Any` as intended.
