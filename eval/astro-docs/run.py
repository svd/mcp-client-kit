"""
Smoke-test runner for generated astro-docs/ wrappers.
Transport: Streamable HTTP  (https://mcp.docs.astro.build/mcp)
Auth: none (public endpoint)

Usage:
    python eval/astro-docs/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "astro-docs.py" — the hyphen makes it unimportable
# by a plain `import`, so load it by path under the name `astro_docs`.
_spec = importlib.util.spec_from_file_location(
    "astro_docs", os.path.join(os.path.dirname(__file__), "astro-docs.py")
)
astro_docs = importlib.util.module_from_spec(_spec)
sys.modules["astro_docs"] = astro_docs
_spec.loader.exec_module(astro_docs)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.docs.astro.build/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — the server exposes only read-only search)
        # Args below are the real pre-scrub probe args from astro-docs.verify.json.

        # search_astro_docs -> list[AstroDocSearchResult]
        # (no discriminator in shapes.json — a single call covers the return shape)
        results = await astro_docs.search_astro_docs(caller, query="content collections")
        print(f"search_astro_docs: {len(results)} item(s)")
        if results:
            first = results[0]
            print(
                f"  first: title={first.get('title')!r} "
                f"source_type={first.get('source_type')!r} "
                f"source_url={first.get('source_url')!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
