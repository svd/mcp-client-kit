"""A real MCP server over stdio, for end-to-end tests.

Deliberately small and deterministic: no clock, no randomness, no network. Run
directly (`python tests/integration/fixtures/stdio_server.py`) or via
`mcpgen ... --stdio "python tests/integration/fixtures/stdio_server.py"`.
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def greet(name: str, excited: bool = False) -> dict:
    """Greet someone by name."""
    message = f"Hello, {name}{'!' if excited else '.'}"
    return {"message": message, "length": len(name)}


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.tool()
def list_records(limit: int = 3) -> list[dict]:
    """Return a list of records."""
    return [{"id": i, "name": f"record-{i}"} for i in range(1, limit + 1)]


@mcp.tool()
def json_payload() -> str:
    """Return a JSON document as a string."""
    return '{"kind": "payload", "items": [1, 2, 3]}'


@mcp.tool()
def styled(name: str, style: Literal["formal", "casual"] = "casual") -> str:
    """Greet with a named style — exercises an enum input schema."""
    return f"Good day, {name}." if style == "formal" else f"hey {name}"


if __name__ == "__main__":
    mcp.run()
