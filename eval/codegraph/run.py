"""
Smoke-test runner for generated codegraph/ wrappers.
Transport: stdio  (codegraph serve --mcp)
Auth: none

Args come from codegraph.verify.json (real, pre-scrub probe args).
No tool in codegraph.shapes.json declares a discriminator, so each tool is
called exactly once.

Usage:
    python eval/codegraph/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file (eval/codegraph/codegraph.py),
# so the artifact dir itself is what has to be importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegraph

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="codegraph serve --mcp")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — all five codegraph tools are read-only)

        # codegraph_search -> Any  (symbol lookup by name)
        search = await codegraph.codegraph_search(caller, query="verify", limit=10)
        print(f"codegraph_search: {type(search).__name__} ({len(str(search))} chars)")

        # codegraph_node -> Any  (source/signature/docstring for one symbol)
        node = await codegraph.codegraph_node(
            caller, symbol="verify_server", includeCode=False
        )
        print(f"codegraph_node: {type(node).__name__} ({len(str(node))} chars)")

        # codegraph_context -> Any  (PRIMARY: composed search + node + callers/callees)
        ctx = await codegraph.codegraph_context(
            caller,
            task="how does artifact verification work",
            maxNodes=10,
            includeCode=True,
        )
        print(f"codegraph_context: {type(ctx).__name__} ({len(str(ctx))} chars)")

        # codegraph_explore -> Any  (survey several related symbols in one capped call)
        explore = await codegraph.codegraph_explore(
            caller, query="verify_server cmd_verify", maxFiles=3
        )
        print(f"codegraph_explore: {type(explore).__name__} ({len(str(explore))} chars)")

        # codegraph_trace -> Any  ("how does <from_> reach <to>?")
        trace = await codegraph.codegraph_trace(
            caller, from_="cmd_verify", to="verify_server"
        )
        print(f"codegraph_trace: {type(trace).__name__} ({len(str(trace))} chars)")


if __name__ == "__main__":
    asyncio.run(main())
