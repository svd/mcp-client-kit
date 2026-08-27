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
# The wrapper module file is "astro-docs.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path as `astro_docs`.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "astro-docs.py")
_spec = importlib.util.spec_from_file_location("astro_docs", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
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
        # Skipped mutating tools: (none — this server exposes only a search tool)
        # Args come from astro-docs.verify.json (real, pre-scrub probe args).

        # search_astro_docs -> list[AstroDocSearchResult]
        results = await astro_docs.search_astro_docs(caller, query="view transitions")
        print(f"search_astro_docs: {len(results)} item(s)")
        for hit in results[:3]:
            print(f"  - {hit.get('title')!r}  {hit.get('source_type')!r}  {hit.get('source_url')!r}")


if __name__ == "__main__":
    asyncio.run(main())
