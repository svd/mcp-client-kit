"""
Smoke-test runner for generated memory/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-memory)
Auth: none

Usage:
    python eval/memory/run.py

Note: the knowledge-graph store starts empty. Seed it first (see
servers/servers.toml `seed` for the memory entry) or read_graph and the
search/open calls will legitimately return zero entities.
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file; put its directory on sys.path so
# "import memory" resolves to memory.py rather than to the package directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-memory")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: add_observations, create_entities,
        # create_relations, delete_entities, delete_observations,
        # delete_relations
        # No discriminated tools in this server: every shaped tool returns the
        # same KnowledgeGraph record, so one call per tool is the full matrix.

        # read_graph -> KnowledgeGraph
        graph = await memory.read_graph(caller)
        print(
            f"read_graph: entities={len(graph.get('entities') or [])} "
            f"relations={len(graph.get('relations') or [])}"
        )

        # search_nodes -> KnowledgeGraph   (args from memory.verify.json)
        found = await memory.search_nodes(caller, query="Ada")
        print(
            f"search_nodes(query='Ada'): entities={len(found.get('entities') or [])} "
            f"relations={len(found.get('relations') or [])}"
        )

        # open_nodes -> KnowledgeGraph   (args from memory.verify.json)
        opened = await memory.open_nodes(
            caller, names=["Ada Lovelace", "Analytical Engine"]
        )
        print(
            f"open_nodes(2 names): entities={len(opened.get('entities') or [])} "
            f"relations={len(opened.get('relations') or [])}"
        )


if __name__ == "__main__":
    asyncio.run(main())
