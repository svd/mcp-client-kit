"""
Smoke-test runner for generated codegraph/ wrappers.
Transport: stdio  (codegraph serve --mcp)
Auth: none

Usage:
    python eval/codegraph/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import codegraph

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="codegraph serve --mcp")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — every codegraph tool is read-only)
        # Args are the real pre-scrub probe args from codegraph.verify.json.
        # No tool is discriminated, so each is called exactly once.
        # projectPath is omitted throughout: the probes ran against the current
        # project, which is what the runner should smoke-test too.

        # codegraph_search -> Any  (observed: str)
        hits = await codegraph.codegraph_search(
            caller,
            query="verify_server",
            limit=5,
        )
        print(f"codegraph_search: {type(hits).__name__} len={len(hits)}")

        # codegraph_node -> Any  (observed: str)
        node = await codegraph.codegraph_node(
            caller,
            symbol="verify_server",
            includeCode=True,
        )
        print(f"codegraph_node: {type(node).__name__} len={len(node)}")

        # codegraph_context -> Any  (observed: str)
        context = await codegraph.codegraph_context(
            caller,
            task="how does verification work",
            maxNodes=5,
            includeCode=True,
        )
        print(f"codegraph_context: {type(context).__name__} len={len(context)}")

        # codegraph_explore -> Any  (observed: str)
        survey = await codegraph.codegraph_explore(
            caller,
            query="verify_server cmd_verify",
            maxFiles=3,
        )
        print(f"codegraph_explore: {type(survey).__name__} len={len(survey)}")

        # codegraph_trace -> Any  (observed: str)
        # Wrapper renames the wire field "from" to the keyword `from_`.
        path = await codegraph.codegraph_trace(
            caller,
            from_="verify_server",
            to="check_ast",
        )
        print(f"codegraph_trace: {type(path).__name__} len={len(path)}")


if __name__ == "__main__":
    asyncio.run(main())
