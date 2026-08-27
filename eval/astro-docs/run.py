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
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "astro-docs.py")
_spec = importlib.util.spec_from_file_location("astro_docs", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
astro_docs = importlib.util.module_from_spec(_spec)
sys.modules["astro_docs"] = astro_docs
_spec.loader.exec_module(astro_docs)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.docs.astro.build/mcp"


def _preview(label: str, results: list) -> None:
    """`search_astro_docs -> list[SearchDocItem]` after the `search_results` unwrap."""
    print(f"{label}: {len(results)} item(s)")
    for item in results[:3]:
        title = item.get("title")
        source_url = item.get("source_url")
        source_type = item.get("source_type")
        content = item.get("content") or ""
        print(
            f"  - title={title!r} source_type={source_type!r} "
            f"source_url={source_url!r} content={len(content)} chars"
        )


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none. astro-docs exposes a single read-only
        # search tool.
        # No tool in astro-docs.shapes.json carries a discriminator, so there are
        # no per-variant calls; both probed queries from astro-docs.verify.json
        # are exercised to cover the full probed arg set.

        # search_astro_docs -> list[SearchDocItem]  (probe 1)
        collections = await astro_docs.search_astro_docs(
            caller, query="content collections"
        )
        _preview("search_astro_docs('content collections')", collections)

        # search_astro_docs -> list[SearchDocItem]  (probe 2)
        transitions = await astro_docs.search_astro_docs(
            caller, query="view transitions astro:page-load"
        )
        _preview("search_astro_docs('view transitions astro:page-load')", transitions)


if __name__ == "__main__":
    asyncio.run(main())
