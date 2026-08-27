"""
Smoke-test runner for generated exa/ wrappers.
Transport: Streamable HTTP  (https://mcp.exa.ai/mcp)
Auth: none (public endpoint)

Args come from eval/exa/exa.verify.json (real, pre-scrub probe args).

Usage:
    python eval/exa/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file, so its own directory goes on the
# path ahead of the package-style parent entry from the skeleton.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))
import exa

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.exa.ai/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — both exa tools are read-only.
        # No discriminated tools: shapes.json records no discriminator/variants,
        # so each tool gets exactly one call with its probed arg set.

        # web_search_exa -> Any  (observed shape: str, ~24.8 KB)
        # Search the web and get clean, ready-to-use content.
        search = await exa.web_search_exa(
            caller,
            query="blog post explaining the Model Context Protocol architecture",
            numResults=3,
        )
        print(f"web_search_exa: {type(search).__name__} len={len(search)}")

        # web_fetch_exa -> Any  (observed shape: str, ~1.6 KB)
        # Read a webpage's full content as clean markdown; follows up on a search.
        fetched = await exa.web_fetch_exa(
            caller,
            urls=["https://modelcontextprotocol.io/introduction"],
            maxCharacters=1500,
        )
        print(f"web_fetch_exa: {type(fetched).__name__} len={len(fetched)}")


if __name__ == "__main__":
    asyncio.run(main())
