"""
Smoke-test runner for generated cloudflare-docs/ wrappers.
Transport: Streamable HTTP  (https://docs.mcp.cloudflare.com/mcp)
Auth: none (public endpoint)

Usage:
    python eval/cloudflare-docs/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "cloudflare-docs.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path as `cloudflare_docs`.
_spec = importlib.util.spec_from_file_location(
    "cloudflare_docs",
    os.path.join(os.path.dirname(__file__), "cloudflare-docs.py"),
)
cloudflare_docs = importlib.util.module_from_spec(_spec)
sys.modules["cloudflare_docs"] = cloudflare_docs
_spec.loader.exec_module(cloudflare_docs)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://docs.mcp.cloudflare.com/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — the server exposes only read-only tools)
        # Args below are the real pre-scrub probe args from cloudflare-docs.verify.json.
        # Neither tool carries a discriminator in shapes.json, so one call each
        # covers every probed return shape. Both return a flat string payload
        # (shapes.json `_observed_shape: "str"`, wrapper annotation `-> Any`).

        # migrate_pages_to_workers_guide -> Any  (observed: str, ~5.7 KB)
        # No parameters; probed_args in shapes.json is {} — nothing to supply.
        guide = await cloudflare_docs.migrate_pages_to_workers_guide(caller)
        print(
            f"migrate_pages_to_workers_guide: {type(guide).__name__} "
            f"len={len(guide) if hasattr(guide, '__len__') else 'n/a'}"
        )
        if isinstance(guide, str) and guide:
            print(f"  first line: {guide.splitlines()[0][:100]!r}")

        # search_cloudflare_documentation -> Any  (observed: str, ~16.5 KB)
        docs = await cloudflare_docs.search_cloudflare_documentation(
            caller, query="How do I bind a KV namespace to a Worker?"
        )
        print(
            f"search_cloudflare_documentation: {type(docs).__name__} "
            f"len={len(docs) if hasattr(docs, '__len__') else 'n/a'}"
        )
        if isinstance(docs, str) and docs:
            print(f"  first line: {docs.splitlines()[0][:100]!r}")


if __name__ == "__main__":
    asyncio.run(main())
