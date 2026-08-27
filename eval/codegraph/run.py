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

# The wrapper module sits next to this file, so put THIS directory on sys.path
# (not its parent) before importing it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codegraph

from mcpgen import McpBridgeCaller


def _preview(text: object, limit: int = 100) -> str:
    """First line of a prose response, truncated — these tools return str."""
    s = str(text).strip().splitlines()
    head = s[0] if s else ""
    return head[:limit] + ("…" if len(head) > limit else "")


async def main() -> None:
    caller = McpBridgeCaller(cmd="codegraph serve --mcp")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — all 5 codegraph tools are read-only)
        # Args are the real probed args from codegraph.verify.json.
        # Every tool returns prose (-> Any, observed shape: str), so each block
        # reports the response type and size plus a first-line preview.

        # codegraph_search -> Any  (observed: str)
        hits = await codegraph.codegraph_search(caller, query="verify", limit=10)
        print(
            f"codegraph_search: {type(hits).__name__} "
            f"{len(str(hits))} chars | {_preview(hits)}"
        )

        # codegraph_node -> Any  (observed: str)
        node = await codegraph.codegraph_node(
            caller, symbol="verify_server", includeCode=True
        )
        print(
            f"codegraph_node: {type(node).__name__} "
            f"{len(str(node))} chars | {_preview(node)}"
        )

        # codegraph_context -> Any  (observed: str)
        context = await codegraph.codegraph_context(
            caller,
            task="how does the verifier check generated wrappers",
            maxNodes=5,
            includeCode=True,
        )
        print(
            f"codegraph_context: {type(context).__name__} "
            f"{len(str(context))} chars | {_preview(context)}"
        )

        # codegraph_explore -> Any  (observed: str)
        explored = await codegraph.codegraph_explore(
            caller, query="verify_server cmd_verify", maxFiles=3
        )
        print(
            f"codegraph_explore: {type(explored).__name__} "
            f"{len(str(explored))} chars | {_preview(explored)}"
        )

        # codegraph_trace -> Any  (observed: str)
        # Two probed arg sets in verify.json — one call each.
        trace1 = await codegraph.codegraph_trace(
            caller, from_="cmd_verify", to="verify_server"
        )
        print(
            f"codegraph_trace(cmd_verify -> verify_server): "
            f"{type(trace1).__name__} {len(str(trace1))} chars | {_preview(trace1)}"
        )

        trace2 = await codegraph.codegraph_trace(
            caller, from_="verify_server", to="check_ast"
        )
        print(
            f"codegraph_trace(verify_server -> check_ast): "
            f"{type(trace2).__name__} {len(str(trace2))} chars | {_preview(trace2)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
