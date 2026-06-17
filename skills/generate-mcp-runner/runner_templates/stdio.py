"""
Smoke-test runner for generated $server_name/ wrappers.
Transport: stdio  ($launch)
Auth: none

Usage:
    python $server_name/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import $module_name

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="$launch")

$demo_calls

if __name__ == "__main__":
    asyncio.run(main())
