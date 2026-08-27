"""
Smoke-test runner for generated context7/ wrappers.
Transport: stdio  (npx -y @upstash/context7-mcp)
Auth: none

Usage:
    python context7/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import context7

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @upstash/context7-mcp")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — every context7 tool is read-only)
        # Args below are the real probed args from context7.verify.json.
        # Neither tool is discriminated and both return prose (-> Any,
        # observed shape: str), so there is one call per tool.

        # resolve-library-id -> Any
        # Must run first: it resolves a package name to a Context7 library ID.
        library = await context7.resolve_library_id(
            caller,
            query="How to configure middleware",
            libraryName="Next.js",
        )
        print(f"resolve_library_id: {type(library).__name__} len={len(str(library))}")

        # query-docs -> Any
        # Uses the probed libraryId directly (same value resolve-library-id returns).
        docs = await context7.query_docs(
            caller,
            libraryId="/vercel/next.js",
            query="How to configure middleware in Next.js",
        )
        print(f"query_docs: {type(docs).__name__} len={len(str(docs))}")

if __name__ == "__main__":
    asyncio.run(main())
