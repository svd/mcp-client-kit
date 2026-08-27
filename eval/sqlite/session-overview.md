# sqlite — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:03:13Z
- **Duration:** 2m 14s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list sqlite --schema` returned **6 tools**, none carrying `annotations`, so
classification fell back to the keyword test plus a semantic read of each description:

```
Tools on sqlite:
  read_query      — Execute a SELECT query on the SQLite database
  list_tables     — List all tables in the SQLite database
  describe_table  — Get the schema information for a specific table
  ⚠ write_query   — Execute an INSERT, UPDATE, or DELETE query [MUTATING]
  ⚠ create_table  — Create a new table in the SQLite database [MUTATING]
  ⚠ append_insight— Add a business insight to the memo [MUTATING]
```

**3 probed, 3 skipped.** `write_query` and `create_table` are mutating by their own
descriptions. `append_insight` is caught by the `add`/`append` keywords and writes to the
server's memo resource; it is exactly the case the runner skill's narrower keyword list would
miss, so it was named explicitly as skip-only. No seed commands were configured; `/tmp/eval.db`
already held two tables (`users`, `products`), so the read tools had data to return.

**Discriminators: N/A.** The only parameter shared by two or more tools is `query`
(`read_query`, `write_query`, `create_table`), which sits on the engine's own denylist, so no
advisory fired — `list --schema` stderr was empty, confirming absence rather than an unread
warning. The description sweep found no parameter naming a response key. Pass 2 was skipped.

## Responses and shape decisions

A bootstrap `call list_tables --out` supplied the real table names for `describe_table`. Both
`list_tables` and `describe_table` returned plain JSON arrays of flat dicts — no vendor
envelope, no double encoding — so `unwrap` stays `[]` on every entry and the generated bodies
`cast(...)` rather than `_dig_list(...)`.

- **`list_tables` → `list[TableRef]`.** One field, `name: str`. Two elements observed.
- **`describe_table` → `list[ColumnInfo]`.** This is `PRAGMA table_info` output:
  `cid:int, name:str, type:str, notnull:int, dflt_value:Any | None, pk:int`. Probed against
  both tables; the shapes were identical apart from element count, and `dflt_value` came back
  null in every row, so it is typed nullable-`Any` rather than guessed.
- **`read_query` → `Any` (unwrap-only, option 3).** The surprise of the run: the deep merge
  produced a confident-looking 9-field record, but that is an artifact of unioning two
  unrelated queries — `SELECT * FROM users` yields `id/name/email/active/score`, `SELECT * FROM
  products` yields `id/title/price/stock/discontinued`. The row shape is dictated entirely by
  the caller's free-text SQL, there are no enumerable variants, and a `TypedDict` here would
  assert this one database's schema as the tool's contract. Left `return_model: null` with the
  reasoning recorded in `_shape_note`.

## Verification

Regeneration reported `shapes: eval/sqlite/sqlite.shapes.json (3 tool(s))`. The module parses
cleanly under `ast.parse`, and the two shaped signatures read `-> list[ColumnInfo]` and
`-> list[TableRef]`; the three mutating tools and `read_query` remain `-> Any`. `probed_args`
needed no scrubbing: generic table names and standard SQL are functional values, not PII.
