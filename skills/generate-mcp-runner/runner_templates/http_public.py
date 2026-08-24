"""
Smoke-test runner for generated $server_name/ wrappers.
Transport: Streamable HTTP  ($launch)
Auth: none (public endpoint)

Usage:
    python $server_name/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import $module_name

from mcpgen import McpBridgeCaller

SERVER_URL = "$launch"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
$demo_calls

if __name__ == "__main__":
    asyncio.run(main())
