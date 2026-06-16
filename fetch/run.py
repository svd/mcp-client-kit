"""
Smoke-test runner for generated fetch/ wrappers.
Transport: stdio  (uvx mcp-server-fetch)
Auth: none

Usage:
    python fetch/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import fetch

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-fetch")

    # fetch → Any
    result = await fetch.fetch(caller, url='<example-url>')
    print(f"fetch: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
