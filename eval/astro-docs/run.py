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
        # Skipped mutating tools: (none — the server exposes a single read-only
        # search tool).
        # Args are the real, pre-scrub probe args from astro-docs.verify.json.
        # search_astro_docs has no discriminator, so both probed queries are
        # replayed against the one return shape (list[AstroDocSearchResult]).

        # search_astro_docs -> list[AstroDocSearchResult]
        collections = await astro_docs.search_astro_docs(
            caller, query="content collections"
        )
        print(f"search_astro_docs('content collections'): {len(collections)} item(s)")
        for hit in collections[:3]:
            print(
                f"  - {hit.get('title')!r}  "
                f"{hit.get('source_type')!r}  {hit.get('source_url')!r}"
            )

        # search_astro_docs -> list[AstroDocSearchResult]  (second probed query)
        transitions = await astro_docs.search_astro_docs(
            caller, query="view transitions astro:page-load"
        )
        print(
            "search_astro_docs('view transitions astro:page-load'): "
            f"{len(transitions)} item(s)"
        )
        for hit in transitions[:3]:
            print(
                f"  - {hit.get('title')!r}  "
                f"{hit.get('source_type')!r}  {hit.get('source_url')!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
