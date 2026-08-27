"""
Smoke-test runner for generated sequential-thinking/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-sequential-thinking)
Auth: none

Usage:
    python eval/sequential-thinking/run.py

Args come from sequential-thinking.verify.json (real, pre-scrub probe args).

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

    # Skipped mutating tools: (none) — `sequentialthinking` is this server's only
    # tool. It appends to an in-process thought log that dies with the subprocess,
    # so it is treated as read-only for smoke-test purposes.
    #
    # No discriminator in shapes.json; verify.json records two probes, emitted
    # below in sequence (linear step, then a branch off thought 1).

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # sequentialthinking -> ThoughtProgress  (probe 1/2: initial linear thought)
        step1 = await sequential_thinking.sequentialthinking(
            caller,
            thought="Step one: enumerate the constraints of the problem.",
            thoughtNumber=1,
            totalThoughts=3,
            nextThoughtNeeded=True,
        )
        print(
            f"sequentialthinking(1): thoughtNumber={step1.get('thoughtNumber')!r}"
            f"  totalThoughts={step1.get('totalThoughts')!r}"
            f"  nextThoughtNeeded={step1.get('nextThoughtNeeded')!r}"
            f"  branches={step1.get('branches')!r}"
            f"  thoughtHistoryLength={step1.get('thoughtHistoryLength')!r}"
        )

        # sequentialthinking -> ThoughtProgress  (probe 2/2: branch off thought 1)
        step2 = await sequential_thinking.sequentialthinking(
            caller,
            thought="Step two: branch to explore an alternative framing.",
            thoughtNumber=2,
            totalThoughts=3,
            nextThoughtNeeded=True,
            branchFromThought=1,
            branchId="alt-a",
            isRevision=False,
            needsMoreThoughts=False,
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
