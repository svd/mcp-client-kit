"""
Smoke-test runner for generated $server_name/ wrappers.
Transport: Streamable HTTP  ($launch)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login $server_name

    # Then run:
    python $server_name/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import $module_name

from mcpgen import McpBridgeCaller, ensure_login

SERVER_URL = "$launch"
SERVER_NAME = "$server_name"


async def main() -> None:
    # Ensure a valid OAuth token is available (silent refresh or browser prompt)
    await ensure_login(SERVER_NAME)
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: one initialize() and one OAuth
    # pre-flight refresh, instead of one per tool call.
    async with caller.connected():
$demo_calls

if __name__ == "__main__":
    asyncio.run(main())
