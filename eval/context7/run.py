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

    # Skipped mutating tools: (none — both tools are read-only)

    # resolve_library_id -> Any
    # Must be called first to obtain a valid library ID for query_docs
    lib_result = await context7.resolve_library_id(
        caller,
        query="HTTP client library for Python",
        libraryName="requests",
    )
    print(f"resolve_library_id: {type(lib_result).__name__}")

    # query_docs -> Any
    docs_result = await context7.query_docs(
        caller,
        libraryId="/psf/requests",
        query="How to make a GET request",
    )
    print(f"query_docs: {type(docs_result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
