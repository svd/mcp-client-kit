"""
Smoke-test runner for generated codegraph/ wrappers.
Transport: stdio  (codegraph-mcp)
Auth: none

Usage:
    python codegraph/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import codegraph

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="codegraph-mcp")

    # codegraph_context → Any
    result = await codegraph.codegraph_context(caller, query='<example-query>', projectPath='<example-project-path>')
    print(f"codegraph_context: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
