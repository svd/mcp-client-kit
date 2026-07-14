"""Tests for `mcpgen list` — network-free; _list_tools is monkeypatched."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from mcpgen.cli import _cmd_list, _list_tools

# ── helpers ───────────────────────────────────────────────────────────────────


def _ns(server: str, schema: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        server=server,
        schema=schema,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
        env=None,
    )


FAKE_TOOLS = [
    {
        "name": "get_user",
        "description": "Get user by ID",
        "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
    },
    {
        "name": "list_users",
        "description": "List all users",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
]


# ── without --schema ──────────────────────────────────────────────────────────


def test_list_without_schema_includes_name_and_description(capsys):
    """Without --schema, output is {name, description} only."""
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        rc = _cmd_list(ns)

    assert rc == 0
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result) == 2
    assert result[0] == {"name": "get_user", "description": "Get user by ID"}
    assert result[1] == {"name": "list_users", "description": "List all users"}


def test_list_without_schema_no_inputschema_in_output(capsys):
    """Without --schema, inputSchema should not be in the output."""
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    for item in result:
        assert "inputSchema" not in item


def test_list_without_schema_empty_description_fallback(capsys):
    """When description is None or absent, it should fallback to empty string."""
    tools = [
        {"name": "no_desc_none", "description": None, "inputSchema": {}},
        {"name": "no_desc_absent", "inputSchema": {}},
    ]
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0] == {"name": "no_desc_none", "description": ""}
    assert result[1] == {"name": "no_desc_absent", "description": ""}


# ── with --schema ─────────────────────────────────────────────────────────────


def test_list_with_schema_includes_inputschema(capsys):
    """With --schema, output includes inputSchema."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        rc = _cmd_list(ns)

    assert rc == 0
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result) == 2
    assert "inputSchema" in result[0]
    assert result[0]["inputSchema"] == FAKE_TOOLS[0]["inputSchema"]
    assert result[1]["inputSchema"] == FAKE_TOOLS[1]["inputSchema"]


def test_list_with_schema_includes_name_and_description(capsys):
    """With --schema, output still includes name and description."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["name"] == "get_user"
    assert result[0]["description"] == "Get user by ID"
    assert result[1]["name"] == "list_users"
    assert result[1]["description"] == "List all users"


def test_list_with_schema_empty_inputschema_fallback(capsys):
    """When inputSchema is None or absent, it should fallback to empty dict."""
    tools = [
        {"name": "no_schema_none", "description": "A tool", "inputSchema": None},
        {"name": "no_schema_absent", "description": "A tool"},
    ]
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["inputSchema"] == {}
    assert result[1]["inputSchema"] == {}


def test_list_with_schema_all_fields_present(capsys):
    """With --schema, each tool has all three fields: name, description, inputSchema."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    for item in result:
        assert set(item.keys()) == {"name", "description", "inputSchema"}


# ── annotations / readOnlyHint ────────────────────────────────────────────────


def _mock_tool(name: str, description: str, annotations=None):
    t = MagicMock()
    t.name = name
    t.description = description
    t.inputSchema = {}
    t.annotations = annotations
    return t


def test_list_tools_surfaces_readonly_hint_annotation():
    """_list_tools must not drop the MCP `annotations` object (readOnlyHint lives there)."""
    annotations = MagicMock()
    annotations.model_dump.return_value = {"readOnlyHint": True}
    tool = _mock_tool("whoami", "Return caller identity", annotations=annotations)

    mock_session = MagicMock()
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield mock_session

    with patch("mcpgen.cli._bridge.session", fake_session):
        result = asyncio.run(_list_tools("acme"))

    assert result[0]["annotations"] == {"readOnlyHint": True}


def test_list_tools_annotations_absent_is_none():
    """A tool with no annotations object surfaces annotations: None (not dropped)."""
    tool = _mock_tool("get_user", "Get user", annotations=None)

    mock_session = MagicMock()
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield mock_session

    with patch("mcpgen.cli._bridge.session", fake_session):
        result = asyncio.run(_list_tools("acme"))

    assert result[0]["annotations"] is None


def test_list_with_schema_surfaces_readonly_hint(capsys):
    """`mcpgen list --schema` output includes annotations when _list_tools provides them."""
    tools = [
        {
            "name": "delete_repo",
            "description": "Delete a repo",
            "inputSchema": {},
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "get_repo",
            "description": "Get a repo",
            "inputSchema": {},
            "annotations": {"readOnlyHint": True},
        },
    ]
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["annotations"] == {"readOnlyHint": False}
    assert result[1]["annotations"] == {"readOnlyHint": True}


def test_list_without_schema_surfaces_readonly_hint(capsys):
    """`mcpgen list` (no --schema) still surfaces annotations when present."""
    tools = [
        {"name": "delete_repo", "description": "Delete a repo", "annotations": {"readOnlyHint": False}},
    ]
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["annotations"] == {"readOnlyHint": False}


def test_list_annotations_omitted_when_absent(capsys):
    """Tools with no annotations key must not gain one in the printed output
    (keeps the existing {name, description[, inputSchema]} contract intact)."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    for item in result:
        assert "annotations" not in item


# ── error handling ────────────────────────────────────────────────────────────


def test_list_handles_list_tools_error(capsys):
    """When _list_tools raises an error, _cmd_list should return 1."""
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=FileNotFoundError("server not found")):
        rc = _cmd_list(ns)

    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()
