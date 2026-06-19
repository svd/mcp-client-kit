"""
Smoke-test runner for generated sqlite/ wrappers.
Transport: stdio  (uvx mcp-server-sqlite --db-path /tmp/eval.db)
Auth: none

Usage:
    python eval/sqlite/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sqlite

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-sqlite --db-path /tmp/eval.db")

    # Skipped mutating tools: append_insight, create_table, write_query

    # list_tables -> list[TableName]
    tables = await sqlite.list_tables(caller)
    print(f"list_tables: {len(tables)} table(s)")

    # describe_table -> list[ColumnInfo]  (probe: users)
    cols_users = await sqlite.describe_table(caller, table_name="users")
    print(f"describe_table(users): {len(cols_users)} column(s)")

    # describe_table -> list[ColumnInfo]  (probe: products)
    cols_products = await sqlite.describe_table(caller, table_name="products")
    print(f"describe_table(products): {len(cols_products)} column(s)")

    # read_query -> list  (probe: SELECT * FROM users LIMIT 2)
    rows_users = await sqlite.read_query(caller, query="SELECT * FROM users LIMIT 2")
    print(f"read_query(users): {len(rows_users)} row(s)")

    # read_query -> list  (probe: SELECT * FROM products LIMIT 2)
    rows_products = await sqlite.read_query(caller, query="SELECT * FROM products LIMIT 2")
    print(f"read_query(products): {len(rows_products)} row(s)")


if __name__ == "__main__":
    asyncio.run(main())
