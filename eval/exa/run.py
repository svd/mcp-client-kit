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
        # No discriminated tools: shapes.json records no discriminator/variants.
        # Each tool was probed twice, so each gets one call per probed arg set.

        # web_search_exa -> Any  (observed shape: str, ~24.8 KB)
        # Search the web and get clean, ready-to-use content.
        search_mcp = await exa.web_search_exa(
            caller,
            query="blog post explaining the Model Context Protocol architecture and design",
            numResults=3,
        )
        print(f"web_search_exa(numResults=3): {type(search_mcp).__name__} len={len(search_mcp)}")

        # web_search_exa -> Any  (second probed arg set: category: filter, default numResults)
        search_rust = await exa.web_search_exa(
            caller,
            query="category:company official documentation site for the Rust programming language",
        )
        print(f"web_search_exa(category:company): {type(search_rust).__name__} len={len(search_rust)}")

        # web_fetch_exa -> Any  (observed shape: str, ~5.4 KB)
        # Read a webpage's full content as clean markdown.
        fetch_capped = await exa.web_fetch_exa(
            caller,
            urls=["https://modelcontextprotocol.io/introduction"],
            maxCharacters=2000,
        )
        print(f"web_fetch_exa(1 url, maxCharacters=2000): {type(fetch_capped).__name__} len={len(fetch_capped)}")

        # web_fetch_exa -> Any  (second probed arg set: batched URLs, no cap)
        fetch_batch = await exa.web_fetch_exa(
            caller,
            urls=[
                "https://modelcontextprotocol.io/introduction",
                "https://www.rust-lang.org/",
            ],
        )
        print(f"web_fetch_exa(2 urls): {type(fetch_batch).__name__} len={len(fetch_batch)}")


if __name__ == "__main__":
    asyncio.run(main())
