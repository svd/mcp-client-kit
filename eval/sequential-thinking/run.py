"""
Smoke-test runner for generated sequential-thinking/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-sequential-thinking)
Auth: none

Usage:
    python eval/sequential-thinking/run.py

Note: the generated wrapper module lives at `sequential-thinking.py`, whose
filename contains a hyphen and so cannot be imported with a plain `import`
statement. We load it directly from its file path under a valid identifier
to avoid a SyntaxError/ModuleNotFoundError.
"""
import asyncio
import importlib.util
import os

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "sequential-thinking.py")
_spec = importlib.util.spec_from_file_location("sequential_thinking_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
sequential_thinking = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sequential_thinking)

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-sequential-thinking")

    # Skipped mutating tools: (none — sequentialthinking is the only tool and is treated as read-only)

    async with caller.connected():
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
