"""
Smoke-test runner for generated $server_name/ wrappers.
Transport: HTTP/SSE  ($launch)
Auth: OAuth (browser flow via mcp-kit)

Usage:
    # First time: authenticate
    mcp-kit login $server_name

    # Then run:
    python $server_name/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import $module_name

from mcp_client_kit import McpBridgeCaller
from mcp_client_kit._bridge import ensure_login

SERVER_URL = "$launch"
SERVER_NAME = "$server_name"


async def main() -> None:
    # Ensure a valid OAuth token is available (silent refresh or browser prompt)
    await ensure_login(SERVER_NAME)
    caller = McpBridgeCaller(url=SERVER_URL)

$demo_calls

if __name__ == "__main__":
    asyncio.run(main())
