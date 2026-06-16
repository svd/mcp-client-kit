"""
Smoke-test runner for generated filesystem/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-filesystem /private/tmp)
Auth: none

Usage:
    python filesystem/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import filesystem

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-filesystem /private/tmp")

    # directory_tree → Any
    result = await filesystem.directory_tree(caller, path='<example-dir-path>')
    print(f"directory_tree: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
