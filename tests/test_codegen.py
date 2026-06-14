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


# ── list-envelope mode (query_radar: data.results is a LIST) ─────────────────

# query_radar returns a LIST under data.results, a different envelope than
# get_entity's data.entity dict. Proves the machinery isn't overfit to one shape.
_QUERY_RADAR = {
    "name": "query_radar",
    "description": "Query radar.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entityType": {"type": "number"},  # schema lies: it's really int
            "query": {"type": "object"},
        },
        "required": ["entityType", "query"],
    },
}
_QUERY_RADAR_SHAPE = {
    "unwrap": ["data", "results"],
    "return_container": "list",
    "return_model": "RadarResult",
    "input_overrides": {"entityType": "int"},
    "fields": {"_id": "str", "fullName": "str"},
    "source": "fixture",
}


def test_render_tool_list_envelope_returns_list_of_model():
    src = codegen.render_tool(_QUERY_RADAR, _QUERY_RADAR_SHAPE)
    # return type is list[Model], not Any and not a bare Model
    assert ") -> list[RadarResult]:" in src
    assert "entityType: int" in src
    # body digs via the list-aware helper and casts to list[Model]
    assert 'result = await caller.call(SERVER, "query_radar", args)' in src
    assert 'return cast("list[RadarResult]", _dig_list(result, (\'data\', \'results\', )))' in src


def test_render_module_list_envelope_emits_dig_list():
    src = codegen.render_module("radar", [_QUERY_RADAR], shapes={"query_radar": _QUERY_RADAR_SHAPE})
    ast.parse(src)
    assert "def _dig_list(obj: Any, path: tuple[str, ...]) -> list:" in src
    assert "class RadarResult(TypedDict, total=False):" in src
    # a pure list envelope needs no dict _dig
    assert "def _dig(obj" not in src


def test_generated_unwrap_matches_unwrap_results_oracle():
    """Diff machinery output vs the hand-built oracle (hand-built oracle:119 _unwrap_results).

    Oracle semantics: already-a-list passes through; result['data']['results'] when
    present; else result.get('results', []) — always a list.
    """
    src = codegen.render_module("radar", [_QUERY_RADAR], shapes={"query_radar": _QUERY_RADAR_SHAPE})
    ns: dict = {}
    exec(compile(src, "radar_gen.py", "exec"), ns)

    class _Caller:
        def __init__(self, resp):
            self.resp = resp

        async def call(self, server, tool, arguments):
            return self.resp

    def oracle(result):  # verbatim hand-built oracle:119 _unwrap_results
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "data" in result:
            data = result["data"]
            if isinstance(data, dict) and "results" in data:
                return data["results"]
        return result.get("results", [])

    for resp in (
        {"data": {"results": [{"_id": "1"}, {"_id": "2"}]}},  # full envelope
        [{"_id": "9"}],                                        # already unwrapped list
        {"results": [{"_id": "7"}]},                           # flattened: top-level results
        {"data": {"other": 1}},                                # envelope, no results -> []
        {"unexpected": 1},                                     # nothing -> []
    ):
        got = asyncio.run(ns["query_radar"](_Caller(resp), entityType=1, query={}))
        assert got == oracle(resp), resp


def test_summarize_shape_collapses_lists_and_records_types():
    obj = {"success": True, "data": {"items": [{"id": "x"}, {"id": "y"}], "n": 3}}
    shape = codegen.summarize_shape(obj)
    assert shape["success"] == "bool"
    assert shape["data"]["n"] == "int"
    assert shape["data"]["items"][0] == {"id": "str"}
    assert shape["data"]["items"][1] == "...x2"
