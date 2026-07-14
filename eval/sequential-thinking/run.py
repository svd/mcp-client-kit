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

    # sequentialthinking -> ThoughtResult  (probed variant 1: initial thought)
    step1 = await sequential_thinking.sequentialthinking(
        caller,
        thought="Step 1: identify the problem.",
        nextThoughtNeeded=True,
        thoughtNumber=1,
        totalThoughts=2,
    )
    print(
        f"sequentialthinking(1): thoughtNumber={step1.get('thoughtNumber')!r}"
        f"  totalThoughts={step1.get('totalThoughts')!r}"
        f"  nextThoughtNeeded={step1.get('nextThoughtNeeded')!r}"
        f"  thoughtHistoryLength={step1.get('thoughtHistoryLength')!r}"
    )

    # sequentialthinking -> ThoughtResult  (probed variant 2: branch from thought 1)
    step2 = await sequential_thinking.sequentialthinking(
        caller,
        thought="Step 2 (branch): explore an alternative approach.",
        nextThoughtNeeded=False,
        thoughtNumber=2,
        totalThoughts=2,
        branchFromThought=1,
        branchId="alt-approach",
    )
    print(
        f"sequentialthinking(2): thoughtNumber={step2.get('thoughtNumber')!r}"
        f"  totalThoughts={step2.get('totalThoughts')!r}"
        f"  nextThoughtNeeded={step2.get('nextThoughtNeeded')!r}"
        f"  branches={step2.get('branches')!r}"
        f"  thoughtHistoryLength={step2.get('thoughtHistoryLength')!r}"
    )


if __name__ == "__main__":
    asyncio.run(main())
