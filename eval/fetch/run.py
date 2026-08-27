"""
Smoke-test runner for generated fetch/ wrappers.
Transport: stdio  (uvx mcp-server-fetch)
Auth: none

Args come from eval/fetch/fetch.verify.json (real, pre-scrub probe args).
No tool in fetch.shapes.json declares a discriminator, but `fetch` was probed
with three argument variants (markdown extraction, `raw=True` HTML, and a
`start_index` continuation), so it is called once per probed variant.

Usage:
    python eval/fetch/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file (eval/fetch/fetch.py), so the
# artifact dir itself is what has to be importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-fetch")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — fetch exposes one read-only tool)

        # fetch -> Any  (observed shape: str — simplified markdown extraction)
        markdown = await fetch.fetch(
            caller, url="https://example.com", max_length=2000
        )
        print(
            f"fetch(markdown): {type(markdown).__name__} "
            f"({len(str(markdown))} chars)"
        )

        # fetch -> Any  (observed shape: str — raw=True, unsimplified HTML)
        raw_html = await fetch.fetch(
            caller, url="https://example.com", max_length=2000, raw=True
        )
        print(
            f"fetch(raw): {type(raw_html).__name__} "
            f"({len(str(raw_html))} chars)"
        )

        # fetch -> Any  (observed shape: str — start_index continuation window)
        window = await fetch.fetch(
            caller, url="https://example.com", max_length=200, start_index=100
        )
        print(
            f"fetch(start_index=100): {type(window).__name__} "
            f"({len(str(window))} chars)"
        )


if __name__ == "__main__":
    asyncio.run(main())
