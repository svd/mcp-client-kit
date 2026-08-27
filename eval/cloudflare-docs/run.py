"""
Smoke-test runner for generated cloudflare-docs/ wrappers.
Transport: Streamable HTTP  (https://docs.mcp.cloudflare.com/mcp)
Auth: none (public endpoint)

Usage:
    python eval/cloudflare-docs/run.py

Args come from cloudflare-docs.verify.json (real, pre-scrub probe args).

Note: the generated wrapper module lives at `cloudflare-docs.py`, whose filename
contains a hyphen and so cannot be imported with a plain `import` statement.
We load it directly from its file path under a valid identifier to avoid a
SyntaxError/ModuleNotFoundError.
"""
import asyncio
import importlib.util
import os

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflare-docs.py")
_spec = importlib.util.spec_from_file_location("cloudflare_docs_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
cloudflare_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cloudflare_docs)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://docs.mcp.cloudflare.com/mcp"


def _preview(text: object, limit: int = 100) -> str:
    s = "" if text is None else str(text)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + "..."


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none - every tool on this server is read-only)
        # No discriminated tools on this server: both tools return prose (str),
        # so shapes.json records no return_model and no variants to fan out over.

        # migrate_pages_to_workers_guide -> Any  (observed shape: str, no args)
        guide = await cloudflare_docs.migrate_pages_to_workers_guide(caller)
        print(f"migrate_pages_to_workers_guide: {type(guide).__name__} len={len(str(guide))}")
        print(f"  head: {_preview(guide, 120)!r}")

        # search_cloudflare_documentation -> Any  (observed shape: str)
        # query from cloudflare-docs.verify.json
        docs = await cloudflare_docs.search_cloudflare_documentation(
            caller,
            query="How do I bind a KV namespace to a Worker?",
        )
        print(f"search_cloudflare_documentation: {type(docs).__name__} len={len(str(docs))}")
        print(f"  head: {_preview(docs, 120)!r}")


if __name__ == "__main__":
    asyncio.run(main())
