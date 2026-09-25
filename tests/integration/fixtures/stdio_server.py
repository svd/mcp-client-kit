"""A real MCP server over stdio, for end-to-end tests.

Deliberately small and deterministic: no clock, no randomness, no network.
Besides the tools it serves three resources: a JSON text resource, a binary
one (FastMCP sends ``bytes`` as a base64 blob) and a URI template. Run
directly (`python tests/integration/fixtures/stdio_server.py`) or via
`mcpgen ... --stdio "python tests/integration/fixtures/stdio_server.py"`.
"""

from __future__ import annotations

import json
from typing import Literal

import anyio
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


@mcp.tool()
async def hang() -> str:
    """Never answer, like a tool waiting for a human; probes of it must time out."""
    await anyio.sleep_forever()
    return "unreachable"


# Eight arbitrary bytes, including ones that are not valid UTF-8, so the blob
# cannot pass for text anywhere along the read path.
LOGO = bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0xFF, 0xFE, 0x01])


@mcp.resource("demo://config", mime_type="application/json", description="Server configuration.")
def config() -> str:
    return json.dumps({"name": "demo", "version": 2, "features": ["greet", "add"]})


@mcp.resource("demo://logo", mime_type="application/octet-stream", description="The demo logo, as raw bytes.")
def logo() -> bytes:
    return LOGO


@mcp.resource("demo://records/{record_id}", mime_type="application/json", description="One record by id.")
def record(record_id: str) -> str:
    return json.dumps({"id": record_id, "name": f"record-{record_id}"})


if __name__ == "__main__":
    mcp.run()
