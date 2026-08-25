# Session Overview: sqlite MCP Server

## Run Metadata

- **Executed:** 2026-08-25T15:42:10Z
- **Duration:** 2m 59s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Environment Issue Found and Fixed

The server would not launch with the manifest's original command
(`uvx mcp-server-sqlite --db-path /tmp/eval.db`): `mcp-server-sqlite` 2025.4.25 declares
`mcp[cli]>=1.6.0` with no upper bound, so an unpinned `uvx` resolves `mcp` 2.1.0 — whose
`Server` API dropped `list_resources()` as a decorator. The server crashed on startup with
`AttributeError: 'Server' object has no attribute 'list_resources'` on every invocation
(`list`, `probe`, and `eval-kit verify`'s roundtrip check all failed identically).
Confirmed the fix by hand: `uvx --with mcp<2 mcp-server-sqlite --db-path /tmp/eval.db`
starts and completes the MCP handshake cleanly. Updated `servers/servers.toml`'s `sqlite`
launch command to add the `--with mcp<2` pin and regenerated `.mcp.eval.json` via
`eval-kit gen-config` before proceeding — otherwise no artifact in this run would have
been possible.

## Server Overview

The `sqlite` MCP server exposes 6 tools for interacting with a local SQLite database file
(`/tmp/eval.db`), no authentication required. The database was empty at the start of this
run (a prior eval's persisted file was not reused), so two tables (`users`, `products`)
with one row each were seeded via `mcpgen call` on `create_table`/`write_query` — a
one-time bootstrap, not part of the probed/shaped tool set.

## Tools Enumerated

| Tool | Description | Action |
|---|---|---|
| `read_query` | Execute a SELECT query | Probed |
| `list_tables` | List all tables | Probed |
| `describe_table` | Get schema info for a table | Probed |
| `write_query` | Execute INSERT/UPDATE/DELETE | Skipped (mutating) |
| `create_table` | Create a new table | Skipped (mutating) — used only for bootstrap seeding |
| `append_insight` | Add a business insight to memo | Skipped (mutating) |

3 tools were probed; 3 were skipped as mutating (no `readOnlyHint` annotations were
present, so the keyword + semantic heuristic applied). `write_query`/`create_table` match
the literal mutating-keyword heuristic; `append_insight` was skipped on semantic grounds
(it writes to a server-side memo even though "add" isn't in the literal keyword list). No
discriminator candidates were flagged by `mcpgen list --schema`.

## Probe Results and Shape Decisions

**`list_tables`** — called with no args, returned exactly 2 `{"name": str}` records.
Modeled as `TableName` TypedDict, `return_container: "list"`, no unwrap needed.

**`describe_table`** — probed against both `users` and `products`; both returned SQLite's
standard `PRAGMA table_info(...)` shape: `cid`/`notnull`/`pk` (int), `name`/`type` (str),
`dflt_value` (`Any | None`, genuinely nullable — columns without defaults return `None`).
Modeled as `ColumnInfo` TypedDict, `return_container: "list"`. Table names are functional
values, not PII.

**`read_query`** — probed with two different SELECTs against `users` and `products`. The
merged observed shape unioned unrelated column sets (`id`/`name`/`email`/`active`/`score`
vs. `id`/`name`/`title`/`price`/`stock`/`discontinued`), because this tool executes
arbitrary caller-supplied SQL — its output shape is a function of the query, not a fixed
contract. A `TypedDict` from two sample queries would misrepresent all other queries.
Decision: `return_model: null`, `return_container: "list"`, return type stays `-> Any`.

## Interesting Observations

- No envelope wrapping was observed anywhere; all three probed tools return bare lists,
  so `unwrap: []` is correct throughout, and codegen uses direct list typing rather than
  `_dig_list`.
- `probed_args` contains only table names and literal SQL query strings — no real ids,
  emails, or names — so no scrubbing was required beyond leaving it as-is.

## Final Verification

The regenerated module (`sqlite.py`, 3194 bytes) parsed cleanly. Signatures:
`describe_table -> list[ColumnInfo]`, `list_tables -> list[TableName]`, `read_query ->
Any` (intentional), and the three mutating tools stay `-> Any`. `eval-kit verify sqlite`
reported verdict **pass**: `ast`, `signatures`, `idempotency`, `pii`, and `roundtrip` all
passed — the roundtrip check made a live call to `describe_table` and got back a typed
result, confirmed only once the `mcp<2` launch pin was in place.
