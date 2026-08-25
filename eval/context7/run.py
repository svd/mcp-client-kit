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
        # resolve_library_id -> Any
        # Must be called before query_docs to obtain a valid library ID.
        library = await context7.resolve_library_id(
            caller, query="how to use hooks", libraryName="React"
        )
        print(f"resolve_library_id: {type(library).__name__}")

        # query_docs -> Any
        docs = await context7.query_docs(
            caller,
            libraryId="/reactjs/react.dev",
            query="React useEffect cleanup function examples",
        )
        print(f"query_docs: {type(docs).__name__}")

if __name__ == "__main__":
    asyncio.run(main())
