"""Tests for the pure codegen functions (no network)."""
import ast
import asyncio

from mcp_client_kit import codegen

# A representative get_entity shape-spec (the EVAL_RADAR differentiator, codified).
_GET_ENTITY = {
    "name": "get_entity",
    "description": "Fetch an entity.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entityId": {"type": "string"},
            "entityType": {"type": "number"},  # schema lies: it's really int
            "fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["entityId", "entityType"],
    },
}
_GET_ENTITY_SHAPE = {
    "unwrap": ["data", "entity"],
    "return_model": "Entity",
    "input_overrides": {"entityType": "int"},
    "fields": {"entityId": "str", "entityType": "int", "benchDurationCurrent": "float | None"},
    "source": "fixture",
}


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


# ── shape-spec consuming mode ────────────────────────────────────────────────

def test_render_model_typeddict():
    src = codegen.render_model("Entity", {"entityId": "str", "x": "float | None"})
    assert src.startswith("class Entity(TypedDict, total=False):")
    assert "    entityId: str" in src
    assert "    x: float | None" in src
    ast.parse(src)


def test_render_model_empty():
    assert codegen.render_model("Empty", {}) == "class Empty(TypedDict, total=False):\n    pass"


def test_render_tool_with_shape_unwraps_and_types():
    src = codegen.render_tool(_GET_ENTITY, _GET_ENTITY_SHAPE)
    # return type is the model, not Any
    assert ") -> Entity:" in src
    # schema-lied number tightened to int via input_overrides
    assert "entityType: int" in src
    assert "float" not in src
    # body unwraps the double envelope and casts
    assert "result = await caller.call(SERVER, \"get_entity\", args)" in src
    assert 'return cast("Entity", _dig(result, (\'data\', \'entity\', )))' in src


def test_render_module_with_shapes_parses_and_has_helpers():
    tools = [_GET_ENTITY, {"name": "whoami", "description": "x", "inputSchema": {}}]
    src = codegen.render_module("radar", tools, shapes={"get_entity": _GET_ENTITY_SHAPE})
    ast.parse(src)
    assert "from typing import Any, TypedDict, cast" in src
    assert "class Entity(TypedDict, total=False):" in src
    assert "def _dig(obj: Any, path: tuple[str, ...]) -> Any:" in src
    # untouched tool stays plain
    assert "async def whoami(caller: McpCaller) -> Any:" in src


def test_render_module_no_shapes_unchanged():
    tools = [{"name": "whoami", "description": "x", "inputSchema": {}}]
    assert codegen.render_module("radar", tools) == codegen.render_module("radar", tools, shapes=None)
    assert "TypedDict" not in codegen.render_module("radar", tools)


def test_generated_unwrap_matches_oracle():
    """Diff machinery output vs the hand-built oracle (hand-built oracle:110 _unwrap_entity).

    Oracle semantics: result['data']['entity'] when present, else result as-is.
    """
    src = codegen.render_module("radar", [_GET_ENTITY], shapes={"get_entity": _GET_ENTITY_SHAPE})
    ns: dict = {}
    exec(compile(src, "radar_gen.py", "exec"), ns)

    class _Caller:
        def __init__(self, resp):
            self.resp = resp

        async def call(self, server, tool, arguments):
            return self.resp

    def oracle(result):
        if isinstance(result, dict) and "data" in result:
            data = result["data"]
            if isinstance(data, dict) and "entity" in data:
                return data["entity"]
        return result

    for resp in (
        {"data": {"entity": {"entityId": "1", "entityType": 1}}},  # full envelope
        {"data": {"results": []}},                                  # envelope, no entity
        {"unexpected": 1},                                          # no envelope at all
    ):
        got = asyncio.run(ns["get_entity"](_Caller(resp), entityId="1", entityType=1))
        assert got == oracle(resp)


def test_summarize_shape_collapses_lists_and_records_types():
    obj = {"success": True, "data": {"items": [{"id": "x"}, {"id": "y"}], "n": 3}}
    shape = codegen.summarize_shape(obj)
    assert shape["success"] == "bool"
    assert shape["data"]["n"] == "int"
    assert shape["data"]["items"][0] == {"id": "str"}
    assert shape["data"]["items"][1] == "...x2"
