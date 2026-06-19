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

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-sequential-thinking")

    # Skipped mutating tools: (none — sequentialthinking is the only tool and is treated as read-only)

    # sequentialthinking -> ThoughtResult
    thought_result = await sequential_thinking.sequentialthinking(
        caller,
        thought="This is the first step of solving the problem.",
        nextThoughtNeeded=False,
        thoughtNumber=1,
        totalThoughts=1,
    )
    print(
        f"sequentialthinking: thoughtNumber={thought_result.get('thoughtNumber')!r}"
        f"  totalThoughts={thought_result.get('totalThoughts')!r}"
        f"  nextThoughtNeeded={thought_result.get('nextThoughtNeeded')!r}"
        f"  thoughtHistoryLength={thought_result.get('thoughtHistoryLength')!r}"
    )


if __name__ == "__main__":
    asyncio.run(main())
