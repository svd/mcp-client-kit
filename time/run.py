"""
Smoke-test runner for generated time/ wrappers.
Transport: stdio  (uvx mcp-server-time)
Auth: none

Usage:
    python time/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import time

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-time")

    # get_current_time → CurrentTime
    result = await time.get_current_time(caller, timezone='<example-timezone>')
    print(f"get_current_time: {result}")

    # convert_time → ConvertedTime
    result = await time.convert_time(caller, source_timezone='<example-timezone>', time='<example-time>', target_timezone='<example-timezone>')
    print(f"convert_time: {result}")


if __name__ == "__main__":
    asyncio.run(main())
