"""The fixture server with a changed tool contract, for live drift detection.

Differences from stdio_server.py, all of which must surface as drift:
  - greet() gains a required `title` parameter
  - styled()'s enum gains "shouty"
  - json_payload() is removed
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def greet(name: str, title: str, excited: bool = False) -> dict:
    """Greet someone by name."""
    message = f"Hello, {title} {name}{'!' if excited else '.'}"
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
def styled(name: str, style: Literal["formal", "casual", "shouty"] = "casual") -> str:
    """Greet with a named style — exercises an enum input schema."""
    if style == "formal":
        return f"Good day, {name}."
    return f"HEY {name.upper()}" if style == "shouty" else f"hey {name}"


if __name__ == "__main__":
    mcp.run()
