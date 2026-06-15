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


# ── discriminator / @overload mode ──────────────────────────────────────────

_GET_ENTITY_DISC_TOOL = {
    "name": "get_entity",
    "description": "Fetch an entity.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entityId": {"type": "string"},
            "entityType": {"type": "number"},
            "fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["entityId", "entityType"],
    },
}
_GET_ENTITY_DISC_SHAPE = {
    "unwrap": ["data", "entity"],
    "discriminator": "entityType",
    "variants": {
        "1": {"return_model": "Person", "fields": {"fullName": "str"}, "source": "fixture"},
        "2": {"return_model": "Position", "fields": {"headline": "str"}, "source": "fixture"},
    },
    "input_overrides": {"entityType": "int"},
}


def test_render_tool_discriminated_emits_overloads():
    src = codegen.render_tool(_GET_ENTITY_DISC_TOOL, _GET_ENTITY_DISC_SHAPE)
    assert "@overload" in src
    assert "entityType: Literal[1]" in src
    assert "entityType: Literal[2]" in src
    assert ") -> Person: ..." in src
    assert ") -> Position: ..." in src
    assert "entityType: int" in src
    assert ") -> Person | Position:" in src
    assert "_dig(result, ('data', 'entity', ))" in src
    assert "@overload" not in src.split("async def get_entity")[-1]  # no overload in impl


def test_render_tool_discriminated_parses():
    src = codegen.render_module(
        "radar", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    ast.parse(src)


def test_render_module_discriminated_imports_and_models():
    src = codegen.render_module(
        "radar", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    assert "from typing import Any, Literal, TypedDict, cast, overload" in src
    assert "class Person(TypedDict, total=False):" in src
    assert "class Position(TypedDict, total=False):" in src
    assert "def _dig(" in src


def test_render_tool_flat_path_not_affected():
    """Flat (non-discriminated) shape → no @overload, byte-identical to pre-change."""
    src = codegen.render_tool(_GET_ENTITY, _GET_ENTITY_SHAPE)
    assert "@overload" not in src
    assert ") -> Entity:" in src


def test_generated_discriminated_unwrap_matches_oracle():
    """Both discriminated variants unwrap the same envelope (same oracle as flat)."""
    src = codegen.render_module(
        "radar", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    ns: dict = {}
    exec(compile(src, "radar_disc_gen.py", "exec"), ns)

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
        {"data": {"entity": {"entityId": "1", "entityType": 1, "fullName": "Alice"}}},
        {"data": {"entity": {"entityId": "2", "entityType": 2, "headline": "Eng"}}},
        {"unexpected": 1},
    ):
        got = asyncio.run(ns["get_entity"](_Caller(resp), entityId="x", entityType=1))
        assert got == oracle(resp), resp


def test_generated_discriminated_fallback_for_unmodeled_variant():
    """Unmodeled entityType (99) hits the int impl — no raise, union returned."""
    src = codegen.render_module(
        "radar", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    ns: dict = {}
    exec(compile(src, "radar_disc_gen.py", "exec"), ns)

    class _Caller:
        def __init__(self, resp):
            self.resp = resp

        async def call(self, server, tool, arguments):
            return self.resp

    resp = {"data": {"entity": {"entityType": 99, "_id": "x"}}}
    got = asyncio.run(ns["get_entity"](_Caller(resp), entityId="x", entityType=99))
    assert got == {"entityType": 99, "_id": "x"}


def test_discriminator_always_required_in_body():
    """Discriminator not in schema required → still treated as required in body (no None-check)."""
    tool = {
        "name": "get_entity",
        "description": "x",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entityId": {"type": "string"},
                "entityType": {"type": "number"},
            },
            "required": ["entityId"],  # entityType deliberately omitted from required
        },
    }
    shape = {
        "unwrap": ["data", "entity"],
        "discriminator": "entityType",
        "variants": {"1": {"return_model": "Person", "fields": {}}},
        "input_overrides": {"entityType": "int"},
    }
    src = codegen.render_tool(tool, shape)
    # impl must have entityType: int with no default (required)
    assert "entityType: int" in src
    # body must NOT have conditional None-check for entityType
    assert "if entityType is not None" not in src


def test_summarize_shape_collapses_lists_and_records_types():
    obj = {"success": True, "data": {"items": [{"id": "x"}, {"id": "y"}], "n": 3}}
    shape = codegen.summarize_shape(obj)
    assert shape["success"] == "bool"
    assert shape["data"]["n"] == "int"
    assert shape["data"]["items"][0] == {"id": "str"}
    assert shape["data"]["items"][1] == "...x2"


# ── merge_shapes ─────────────────────────────────────────────────────────────

def test_merge_shapes_identity_single():
    s = {"a": "str", "b": "int"}
    assert codegen.merge_shapes([s]) is s


def test_merge_shapes_empty_list():
    assert codegen.merge_shapes([]) == "Any"


def test_merge_shapes_key_union():
    """Keys from different probes are unioned (both present → both in result)."""
    a = {"a": "str"}
    b = {"b": "int"}
    assert codegen.merge_shapes([a, b]) == {"a": "str", "b": "int"}


def test_merge_shapes_same_key_same_type():
    a = {"x": "str"}
    b = {"x": "str"}
    assert codegen.merge_shapes([a, b]) == {"x": "str"}


def test_merge_shapes_null_widens_to_optional():
    """str observed in one probe, NoneType in another → str | None."""
    a = {"x": "str"}
    b = {"x": "NoneType"}
    assert codegen.merge_shapes([a, b]) == {"x": "str | None"}


def test_merge_shapes_only_null():
    """Only NoneType ever observed → Any | None (type unknown, nullable)."""
    assert codegen.merge_shapes([{"x": "NoneType"}, {"x": "NoneType"}]) == {"x": "Any | None"}


def test_merge_shapes_int_float_widening():
    """int + float → float (numeric widening; JSON may deliver either)."""
    a = {"n": "int"}
    b = {"n": "float"}
    assert codegen.merge_shapes([a, b]) == {"n": "float"}


def test_merge_shapes_int_float_null_widening():
    a = {"n": "int"}
    b = {"n": "float"}
    c = {"n": "NoneType"}
    assert codegen.merge_shapes([a, b, c]) == {"n": "float | None"}


def test_merge_shapes_conflict_becomes_any():
    """str vs int (non-numeric) conflict → Any."""
    a = {"x": "str"}
    b = {"x": "int"}
    assert codegen.merge_shapes([a, b]) == {"x": "Any"}


def test_merge_shapes_conflict_with_null_becomes_any_or_none():
    a = {"x": "str"}
    b = {"x": "int"}
    c = {"x": "NoneType"}
    assert codegen.merge_shapes([a, b, c]) == {"x": "Any | None"}


def test_merge_shapes_nested_dict():
    """Nested dicts merge recursively."""
    a = {"data": {"id": "str", "name": "str"}}
    b = {"data": {"id": "str", "count": "int"}}
    result = codegen.merge_shapes([a, b])
    assert result == {"data": {"id": "str", "name": "str", "count": "int"}}


def test_merge_shapes_list_element_shapes_merged():
    """List shapes: element shapes are merged, sentinels discarded."""
    a = [{"id": "str", "name": "str"}, "...x10"]
    b = [{"id": "str", "score": "float"}]
    result = codegen.merge_shapes([a, b])
    assert result == [{"id": "str", "name": "str", "score": "float"}]


def test_merge_shapes_list_empty_sentinel():
    a = ["<empty>"]
    b = [{"id": "str"}]
    result = codegen.merge_shapes([a, b])
    assert result == [{"id": "str"}]


def test_merge_shapes_structural_conflict_is_any():
    """dict vs scalar → Any."""
    a = {"x": "str"}
    b = "int"
    assert codegen.merge_shapes([a, b]) == "Any"


# ── probe_skeleton ───────────────────────────────────────────────────────────

def test_probe_skeleton_single_probe_probed_args_is_dict():
    """Single probe → probed_args is a dict (byte-stable with existing files)."""
    args = {"entityId": "abc", "entityType": 1}
    shape = {"data": {"id": "str"}}
    skeleton = codegen.probe_skeleton("get_entity", [args], [shape])
    entry = skeleton["get_entity"]
    assert entry["probed_args"] == args
    assert not isinstance(entry["probed_args"], list)


def test_probe_skeleton_multi_probe_probed_args_is_list():
    """Two probes → probed_args is a list of both arg dicts."""
    a1 = {"entityId": "abc", "entityType": 1}
    a2 = {"entityId": "def", "entityType": 1}
    s1 = {"data": {"id": "str", "name": "str"}}
    s2 = {"data": {"id": "str", "score": "float"}}
    skeleton = codegen.probe_skeleton("get_entity", [a1, a2], [s1, s2])
    entry = skeleton["get_entity"]
    assert entry["probed_args"] == [a1, a2]


def test_probe_skeleton_fields_reflect_merged_top_level_scalars():
    """fields = top-level scalars from merged _observed_shape."""
    s1 = {"id": "str", "name": "str", "nested": {"a": "int"}}
    s2 = {"id": "str", "score": "float", "nullable": "NoneType"}
    skeleton = codegen.probe_skeleton("tool", [{}], [s1, s2])
    fields = skeleton["tool"]["fields"]
    assert fields["id"] == "str"
    assert fields["name"] == "str"
    assert fields["score"] == "float"
    assert fields["nullable"] == "Any | None"
    assert "nested" not in fields  # nested dict excluded from fields


def test_probe_skeleton_structure():
    """Skeleton has all expected keys."""
    skeleton = codegen.probe_skeleton("whoami", [{}], [{}])
    entry = skeleton["whoami"]
    for key in ("unwrap", "return_model", "input_overrides", "fields", "source",
                "probed_args", "_observed_shape"):
        assert key in entry
    assert entry["unwrap"] == []
    assert entry["source"] == "live"
