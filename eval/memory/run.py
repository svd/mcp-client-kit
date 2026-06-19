"""
Smoke-test runner for generated memory/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-memory)
Auth: none

Usage:
    python eval/memory/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import memory

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-memory")

    # Skipped mutating tools: add_observations, create_entities, create_relations,
    #                         delete_entities, delete_observations, delete_relations

    # read_graph -> KnowledgeGraph
    graph = await memory.read_graph(caller)
    print(f"read_graph: entities={len(graph.get('entities') or [])}  relations={len(graph.get('relations') or [])}")

    # search_nodes -> KnowledgeGraph  (args from verify.json)
    search_result = await memory.search_nodes(caller, query="Alice")
    print(f"search_nodes: entities={len(search_result.get('entities') or [])}  relations={len(search_result.get('relations') or [])}")

    # open_nodes -> KnowledgeGraph  (args from verify.json)
    nodes = await memory.open_nodes(caller, names=["Alice", "ProjectX"])
    print(f"open_nodes: entities={len(nodes.get('entities') or [])}  relations={len(nodes.get('relations') or [])}")


if __name__ == "__main__":
    asyncio.run(main())
