"""
Smoke-test runner for generated fetch/ wrappers.
Transport: stdio  (uvx mcp-server-fetch)
Auth: none

Usage:
    python eval/fetch/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import fetch

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-fetch")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — fetch is the only tool and is read-only)

        # fetch -> Any
        page = await fetch.fetch(caller, url="https://example.com")
        print(f"fetch: {type(page).__name__}")

if __name__ == "__main__":
    asyncio.run(main())
