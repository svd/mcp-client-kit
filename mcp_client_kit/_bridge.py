"""Thin bridge to an external MCP client.

This is the *only* module that imports mcp_client. It sits behind the
McpCaller seam so the rest of mcp-client-kit (and all generated wrappers) stay
decoupled from it. Replace with a FastMCP-backed implementation when the codegen
skill has proven itself — nothing else should need to change. See VERDICT.md #2.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# mcp_client lives outside this project; add its package root to sys.path.
_EXTERNAL_SCRIPTS = Path.home() / "src" / "internal-project" / "scripts"
if _EXTERNAL_SCRIPTS.is_dir() and str(_EXTERNAL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_EXTERNAL_SCRIPTS))

from mcp_client.mcp_client import (  # noqa: E402
    _authenticated_session,
    call_tool,
    parse_tool_result,
)


class McpBridgeCaller:
    """McpCaller implementation backed by mcp_client.call_tool."""

    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        return await call_tool(server, tool, arguments)


@asynccontextmanager
async def session(server: str):
    """Yield a live authenticated MCP ClientSession for introspection/probing.

    Auth (including pre-flight refresh) is handled by mcp_client.
    """
    async with _authenticated_session(server) as s:
        yield s


def parse(content_items: list) -> Any:
    """Parse an MCP tool result's content list into JSON. Re-exported for probes."""
    content = [
        {"type": item.type, "text": getattr(item, "text", "")}
        for item in content_items
    ]
    return parse_tool_result(content)
