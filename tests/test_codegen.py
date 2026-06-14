"""Tests for the pure codegen functions (no network)."""
import ast

from mcp_client_kit import codegen


def test_py_type_scalars_and_containers():
    assert codegen.py_type({"type": "string"}) == "str"
    assert codegen.py_type({"type": "integer"}) == "int"
    assert codegen.py_type({"type": "number"}) == "float"
    assert codegen.py_type({"type": "boolean"}) == "bool"
    assert codegen.py_type({"type": "array", "items": {"type": "string"}}) == "list[str]"
    assert codegen.py_type({"type": "object"}) == "dict"
    assert codegen.py_type({}) == "Any"
    assert codegen.py_type({"anyOf": [{"type": "string"}]}) == "Any"
    assert codegen.py_type({"type": ["string", "null"]}) == "str | None"


def test_sanitize_identifiers():
    assert codegen.sanitize("get-current-user") == "get_current_user"
    assert codegen.sanitize("class") == "class_"
    assert codegen.sanitize("2fa") == "_2fa"


def test_render_tool_required_then_optional():
    tool = {
        "name": "get-entity",
        "description": "Fetch an entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entityId": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["entityId"],
        },
    }
    src = codegen.render_tool(tool)
    assert "async def get_entity(caller: McpCaller, *, entityId: str, fields: list[str] | None = None)" in src
    assert 'args: dict[str, Any] = {"entityId": entityId}' in src
    assert 'if fields is not None:' in src
    assert 'return await caller.call(SERVER, "get-entity", args)' in src


def test_render_tool_no_args():
    src = codegen.render_tool({"name": "whoami", "description": "Who am I.", "inputSchema": {}})
    assert "async def whoami(caller: McpCaller) -> Any:" in src
    assert 'caller.call(SERVER, "whoami", {})' in src


def test_render_module_parses():
    tools = [
        {"name": "whoami", "description": "x", "inputSchema": {}},
        {"name": "get-entity", "description": "y",
         "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                         "required": ["id"]}},
    ]
    src = codegen.render_module("radar", tools)
    ast.parse(src)  # must be valid Python
    assert "SERVER = 'radar'" in src


def test_summarize_shape_collapses_lists_and_records_types():
    obj = {"success": True, "data": {"items": [{"id": "x"}, {"id": "y"}], "n": 3}}
    shape = codegen.summarize_shape(obj)
    assert shape["success"] == "bool"
    assert shape["data"]["n"] == "int"
    assert shape["data"]["items"][0] == {"id": "str"}
    assert shape["data"]["items"][1] == "...x2"
