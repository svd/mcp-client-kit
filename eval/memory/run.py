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

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-memory")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: add_observations, create_entities,
        # create_relations, delete_entities, delete_observations, delete_relations
        # Args for search_nodes/open_nodes come from memory.verify.json (real probe args).

        # read_graph -> KnowledgeGraph  (no args)
        graph = await memory.read_graph(caller)
        print(
            f"read_graph: entities={len(graph.get('entities') or [])} "
            f"relations={len(graph.get('relations') or [])}"
        )

        # search_nodes -> KnowledgeGraph
        found = await memory.search_nodes(caller, query="Ada")
        print(
            f"search_nodes('Ada'): entities={len(found.get('entities') or [])} "
            f"relations={len(found.get('relations') or [])}"
        )

        # open_nodes -> KnowledgeGraph
        nodes = await memory.open_nodes(caller, names=["Ada Lovelace", "Analytical Engine"])
        print(
            f"open_nodes(2 names): entities={len(nodes.get('entities') or [])} "
            f"relations={len(nodes.get('relations') or [])}"
        )

if __name__ == "__main__":
    asyncio.run(main())
