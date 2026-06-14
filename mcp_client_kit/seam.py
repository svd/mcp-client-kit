"""The client seam.

Generated wrapper modules depend ONLY on this Protocol — never on a concrete
client. That keeps generated code reusable and lets the auth/transport backend
swap (mcp_client today, FastMCP tomorrow) without regenerating wrappers.
See doc/VERDICT.md "Fixed decisions" #3.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class McpCaller(Protocol):
    """Anything that can invoke an MCP tool and return its parsed result."""

    async def call(self, server: str, tool: str, arguments: dict) -> Any: ...
