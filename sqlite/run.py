"""
Smoke-test runner for generated sqlite/ wrappers.
Transport: stdio  (uvx mcp-server-sqlite --db-path /tmp/eval.db)
Auth: none

Usage:
    python sqlite/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sqlite

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-sqlite --db-path /tmp/eval.db")

    # describe_table → Any
    result = await sqlite.describe_table(caller, table_name='<example-table-name>')
    print(f"describe_table: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
