"""
Smoke-test runner for generated sqlite/ wrappers.
Transport: stdio  (uvx --with 'mcp<2' mcp-server-sqlite --db-path /tmp/eval.db)
Auth: none

Usage:
    python eval/sqlite/run.py
"""
import asyncio
import os
import sys

# The wrapper module lives next to this file (eval/sqlite/sqlite.py), so this
# directory — not its parent — goes on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx --with 'mcp<2' mcp-server-sqlite --db-path /tmp/eval.db")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: append_insight, create_table, write_query

        # list_tables -> list[TableRef]  (no args)
        tables = await sqlite.list_tables(caller)
        print(f"list_tables: {len(tables)} table(s)")

        # describe_table -> list[ColumnInfo]  (probe 1: table_name='users')
        cols_users = await sqlite.describe_table(caller, table_name="users")
        print(f"describe_table(users): {len(cols_users)} column(s)")

        # describe_table -> list[ColumnInfo]  (probe 2: table_name='products')
        cols_products = await sqlite.describe_table(caller, table_name="products")
        print(f"describe_table(products): {len(cols_products)} column(s)")

        # read_query -> Any  (probe 1: users projection)
        # Row keys follow the caller's SELECT projection, so the shape spec
        # leaves this Any on purpose — the two probes returned disjoint
        # column sets, which is why no TypedDict was asserted.
        users_rows = await sqlite.read_query(caller, query="SELECT * FROM users LIMIT 5")
        print(f"read_query(users): {type(users_rows).__name__} with {len(users_rows)} row(s)")

        # read_query -> Any  (probe 2: products projection)
        product_rows = await sqlite.read_query(caller, query="SELECT * FROM products LIMIT 5")
        print(f"read_query(products): {type(product_rows).__name__} with {len(product_rows)} row(s)")


if __name__ == "__main__":
    asyncio.run(main())
