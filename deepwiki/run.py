"""
Smoke-test runner for generated deepwiki/ wrappers.
Transport: HTTP/SSE  (https://mcp.deepwiki.com/mcp)
Auth: none (public endpoint)

Usage:
    python deepwiki/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import deepwiki

from mcp_client_kit import McpBridgeCaller

SERVER_URL = "https://mcp.deepwiki.com/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # ask_question → Any
    result = await deepwiki.ask_question(caller, repoName='<example-repo>', question='<example-question>')
    print(f"ask_question: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
