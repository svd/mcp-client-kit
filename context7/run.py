"""
Smoke-test runner for generated context7/ wrappers.
Transport: stdio  (npx -y @upstash/context7-mcp)
Auth: none

Usage:
    python context7/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import context7

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @upstash/context7-mcp")

    # query-docs → Any
    result = await context7.query_docs(caller, libraryId='react', query='how to call')
    print(f"query-docs: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
