"""
Smoke-test runner for generated exa/ wrappers.
Transport: Streamable HTTP  (https://mcp.exa.ai/mcp)
Auth: none (public endpoint)

Usage:
    python eval/exa/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import exa

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.exa.ai/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none -- both exa tools are read-only)
        # Args below are the real probed values from exa.verify.json.
        # Neither tool is discriminated, so one call each.

        # web_search_exa -> Any  (observed shape: str)
        results = await exa.web_search_exa(
            caller,
            query="blog post comparing React and Vue performance",
            numResults=2,
        )
        print(f"web_search_exa: {type(results).__name__} len={len(results)}")
        print(f"  head: {str(results)[:200]!r}")

        # web_fetch_exa -> Any  (observed shape: str)
        page = await exa.web_fetch_exa(
            caller,
            urls=["https://example.com"],
            maxCharacters=500,
        )
        print(f"web_fetch_exa: {type(page).__name__} len={len(page)}")
        print(f"  head: {str(page)[:200]!r}")


if __name__ == "__main__":
    asyncio.run(main())
