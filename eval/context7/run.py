"""
Smoke-test runner for generated context7/ wrappers.
Transport: stdio  (npx -y @upstash/context7-mcp)
Auth: none

Usage:
    python eval/context7/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file; its own directory goes first so
# "import context7" resolves to context7.py, not the eval/context7 package dir.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context7

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @upstash/context7-mcp")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — context7 exposes read-only tools only)
        # Args below come from eval/context7/context7.verify.json (real probe args).
        # No tool in this server has a discriminator, so one call per tool.

        # resolve-library-id -> Any  (observed shape: str)
        # Discovery step: maps a human library name to a Context7 library id.
        library = await context7.resolve_library_id(
            caller,
            libraryName="Next.js",
            query="How to configure middleware",
        )
        print(f"resolve-library-id: {type(library).__name__} len={len(library)}")

        # query-docs -> Any  (observed shape: str)
        # Depends on a library id of the form returned by resolve-library-id.
        docs = await context7.query_docs(
            caller,
            libraryId="/vercel/next.js",
            query="How to configure middleware in Next.js",
        )
        print(f"query-docs: {type(docs).__name__} len={len(docs)}")


if __name__ == "__main__":
    asyncio.run(main())
