"""
Smoke-test runner for generated sequential-thinking/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-sequential-thinking)
Auth: none

Usage:
    python sequential-thinking/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sequential_thinking

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-sequential-thinking")

    # sequentialthinking → SequentialThinkingResult
    result = await sequential_thinking.sequentialthinking(caller, thought='<example-thought-text>', nextThoughtNeeded=False, thoughtNumber=1, totalThoughts=1)
    print(f"sequentialthinking: {result}")


if __name__ == "__main__":
    asyncio.run(main())
