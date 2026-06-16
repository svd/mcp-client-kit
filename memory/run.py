"""
Smoke-test runner for generated memory/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-memory)
Auth: none

Usage:
    python memory/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import memory

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-memory")

    # open_nodes → KnowledgeGraph
    result = await memory.open_nodes(caller, names=['<example-node-name>'])
    print(f"open_nodes: {result}")

    # read_graph → KnowledgeGraph
    result = await memory.read_graph(caller)
    print(f"read_graph: {result}")


if __name__ == "__main__":
    asyncio.run(main())
