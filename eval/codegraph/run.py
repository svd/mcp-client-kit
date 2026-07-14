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

    # Skipped mutating tools: (none -- all codegraph tools are read-only)

    # codegraph_search -> Any
    search = await codegraph.codegraph_search(caller, query="verify")
    print(f"codegraph_search: {type(search).__name__}")

    # codegraph_context -> Any
    ctx = await codegraph.codegraph_context(
        caller, task="how does the verify.py roundtrip check work"
    )
    print(f"codegraph_context: {type(ctx).__name__}")

    # codegraph_node -> Any
    node = await codegraph.codegraph_node(caller, symbol="check_roundtrip")
    print(f"codegraph_node: {type(node).__name__}")

    # codegraph_explore -> Any
    explore = await codegraph.codegraph_explore(caller, query="verify.py ServerSpec")
    print(f"codegraph_explore: {type(explore).__name__}")

    # codegraph_trace -> Any
    trace = await codegraph.codegraph_trace(
        caller, from_="check_roundtrip", to="CheckResult"
    )
    print(f"codegraph_trace: {type(trace).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
