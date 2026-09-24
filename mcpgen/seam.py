"""The client seam.

Generated wrapper modules depend ONLY on these Protocols — never on a concrete
client. That keeps generated code reusable and lets the auth/transport backend
swap (official `mcp` SDK via `_bridge.py` today; another backend later) without regenerating wrappers.
See doc/VERDICT.md "Fixed decisions" #3.

`McpCaller` covers tools and `McpResourceReader` covers resources; they stay
separate so a custom caller written against `McpCaller` alone keeps satisfying
every module that exposes no resources.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class McpCaller(Protocol):
    """Anything that can invoke an MCP tool and return its parsed result."""

    async def call(self, server: str, tool: str, arguments: dict) -> Any: ...


@runtime_checkable
class McpResourceReader(Protocol):
    """Anything that can read an MCP resource by URI and return its parsed contents."""

    async def read_resource(self, server: str, uri: str) -> Any: ...
