"""
Smoke-test runner for generated $server_name/ wrappers.
Transport: HTTP/SSE  ($launch)
Auth: none (public endpoint)

Usage:
    python $server_name/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import $module_name

from mcp_client_kit import McpBridgeCaller

SERVER_URL = "$launch"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

$demo_calls

if __name__ == "__main__":
    asyncio.run(main())
