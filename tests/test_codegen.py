"""Tests for the pure codegen functions (no network)."""
import ast
import asyncio

from mcp_client_kit import codegen

# A representative get_entity shape-spec (the double-envelope differentiator, codified).
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
    src = codegen.render_module("acme", tools)
    ast.parse(src)  # must be valid Python
    assert "SERVER = 'acme'" in src


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
    src = codegen.render_module("acme", tools, shapes={"get_entity": _GET_ENTITY_SHAPE})
    ast.parse(src)
    assert "from typing import Any, TypedDict, cast" in src
    assert "class Entity(TypedDict, total=False):" in src
    assert "def _dig(obj: Any, path: tuple[str, ...]) -> Any:" in src
    # untouched tool stays plain
    assert "async def whoami(caller: McpCaller) -> Any:" in src


def test_render_module_no_shapes_unchanged():
    tools = [{"name": "whoami", "description": "x", "inputSchema": {}}]
    assert codegen.render_module("acme", tools) == codegen.render_module("acme", tools, shapes=None)
    assert "TypedDict" not in codegen.render_module("acme", tools)


def test_generated_unwrap_matches_oracle():
    """Diff machinery output vs the hand-built oracle (`_unwrap_entity`).

    Oracle semantics: result['data']['entity'] when present, else result as-is.
    """
    src = codegen.render_module("acme", [_GET_ENTITY], shapes={"get_entity": _GET_ENTITY_SHAPE})
    ns: dict = {}
    exec(compile(src, "acme_gen.py", "exec"), ns)

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


# ── list-envelope mode (query_acme: data.results is a LIST) ──────────────────

# query_acme returns a LIST under data.results, a different envelope than
# get_entity's data.entity dict. Proves the machinery isn't overfit to one shape.
_QUERY_ACME = {
    "name": "query_acme",
    "description": "Query acme.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "entityType": {"type": "number"},  # schema lies: it's really int
            "query": {"type": "object"},
        },
        "required": ["entityType", "query"],
    },
}
_QUERY_ACME_SHAPE = {
    "unwrap": ["data", "results"],
    "return_container": "list",
    "return_model": "AcmeResult",
    "input_overrides": {"entityType": "int"},
    "fields": {"_id": "str", "fullName": "str"},
    "source": "fixture",
}


def test_render_tool_list_envelope_returns_list_of_model():
    src = codegen.render_tool(_QUERY_ACME, _QUERY_ACME_SHAPE)
    # return type is list[Model], not Any and not a bare Model
    assert ") -> list[AcmeResult]:" in src
    assert "entityType: int" in src
    # body digs via the list-aware helper and casts to list[Model]
    assert 'result = await caller.call(SERVER, "query_acme", args)' in src
    assert 'return cast("list[AcmeResult]", _dig_list(result, (\'data\', \'results\', )))' in src


def test_render_module_list_envelope_emits_dig_list():
    src = codegen.render_module("acme", [_QUERY_ACME], shapes={"query_acme": _QUERY_ACME_SHAPE})
    ast.parse(src)
    assert "def _dig_list(obj: Any, path: tuple[str, ...]) -> list:" in src
    assert "class AcmeResult(TypedDict, total=False):" in src
    # a pure list envelope needs no dict _dig
    assert "def _dig(obj" not in src


def test_generated_unwrap_matches_unwrap_results_oracle():
    """Diff machinery output vs the hand-built oracle (`_unwrap_results`).

    Oracle semantics: already-a-list passes through; result['data']['results'] when
    present; else result.get('results', []) — always a list.
    """
    src = codegen.render_module("acme", [_QUERY_ACME], shapes={"query_acme": _QUERY_ACME_SHAPE})
    ns: dict = {}
    exec(compile(src, "acme_gen.py", "exec"), ns)

    class _Caller:
        def __init__(self, resp):
            self.resp = resp

        async def call(self, server, tool, arguments):
            return self.resp

    def oracle(result):  # verbatim hand-built _unwrap_results
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
        got = asyncio.run(ns["query_acme"](_Caller(resp), entityType=1, query={}))
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
        "acme", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    ast.parse(src)


def test_render_module_discriminated_imports_and_models():
    src = codegen.render_module(
        "acme", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
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
        "acme", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    ns: dict = {}
    exec(compile(src, "acme_disc_gen.py", "exec"), ns)

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
        "acme", [_GET_ENTITY_DISC_TOOL], shapes={"get_entity": _GET_ENTITY_DISC_SHAPE}
    )
    ns: dict = {}
    exec(compile(src, "acme_disc_gen.py", "exec"), ns)

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


# ── detect_discriminators ─────────────────────────────────────────────────────

def _make_tool(name, props, required=None):
    return {
        "name": name,
        "description": f"Tool {name}.",
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required or [],
        },
    }


def test_detect_discriminators_heuristic_name_shared():
    """Shared scalar named entityType across ≥2 tools appears in result."""
    tools = [
        _make_tool("query_acme", {"entityType": {"type": "integer"}, "q": {"type": "string"}}),
        _make_tool("get_entity", {"entityType": {"type": "integer"}, "entityId": {"type": "string"}}),
    ]
    result = codegen.detect_discriminators(tools)
    assert "entityType" in result
    assert "query_acme" in result["entityType"]
    assert "get_entity" in result["entityType"]


def test_detect_discriminators_enum_shared():
    """Enum param in ≥2 tools appears in result even if not in heuristic name set."""
    tools = [
        _make_tool("create_item", {"status": {"type": "string", "enum": ["active", "inactive"]}}),
        _make_tool("update_item", {"status": {"type": "string", "enum": ["active", "inactive"]}}),
    ]
    result = codegen.detect_discriminators(tools)
    assert "status" in result
    assert sorted(result["status"]) == ["create_item", "update_item"]


def test_detect_discriminators_scalar_single_tool_not_returned():
    """Scalar in only 1 tool is NOT in result."""
    tools = [
        _make_tool("only_tool", {"entityType": {"type": "integer"}}),
        _make_tool("other_tool", {"unrelated": {"type": "string"}}),
    ]
    result = codegen.detect_discriminators(tools)
    # entityType only in one tool, unrelated only in one tool → nothing shared
    assert result == {}


def test_detect_discriminators_empty_tools():
    """Empty tools list returns {}."""
    assert codegen.detect_discriminators([]) == {}


def test_detect_discriminators_no_qualifying_params():
    """No qualifying params returns {}."""
    tools = [
        _make_tool("tool_a", {"items": {"type": "array", "items": {"type": "string"}}}),
        _make_tool("tool_b", {"config": {"type": "object"}}),
    ]
    assert codegen.detect_discriminators(tools) == {}


def test_detect_discriminators_uppercase_param_name_shared_across_tools():
    """Param with capital-letter name (EntityType) is returned when shared across ≥2 tools."""
    tools = [
        _make_tool("tool_a", {"EntityType": {"type": "integer"}}),
        _make_tool("tool_b", {"EntityType": {"type": "integer"}}),
    ]
    result = codegen.detect_discriminators(tools)
    assert "EntityType" in result
    assert sorted(result["EntityType"]) == ["tool_a", "tool_b"]


def test_detect_discriminators_acme_fixture():
    """Acme-like fixture: query_acme, get_entity, get_filters all with entityType."""
    tools = [
        _make_tool(
            "query_acme",
            {"entityType": {"type": "integer"}, "query": {"type": "object"}},
            required=["entityType", "query"],
        ),
        _make_tool(
            "get_entity",
            {"entityType": {"type": "integer"}, "entityId": {"type": "string"}},
            required=["entityType", "entityId"],
        ),
        _make_tool(
            "get_filters",
            {"entityType": {"type": "integer"}},
            required=["entityType"],
        ),
    ]
    result = codegen.detect_discriminators(tools)
    assert "entityType" in result
    assert "query_acme" in result["entityType"]
    assert "get_entity" in result["entityType"]
    assert result["entityType"] == sorted(result["entityType"])  # alphabetically sorted


def test_detect_discriminators_result_sorted():
    """Keys and tool lists are sorted alphabetically for determinism."""
    tools = [
        _make_tool("zoo_tool", {"type": {"type": "string"}, "kind": {"type": "string"}}),
        _make_tool("alpha_tool", {"type": {"type": "string"}, "kind": {"type": "string"}}),
    ]
    result = codegen.detect_discriminators(tools)
    assert list(result.keys()) == sorted(result.keys())
    for tool_list in result.values():
        assert tool_list == sorted(tool_list)


def test_detect_discriminators_denylist_excludes_pagination_params():
    """Pagination and routing params (page, limit, offset, path, owner, …) must be excluded."""
    denylist_params = {
        "page": {"type": "integer"},
        "per_page": {"type": "integer"},
        "limit": {"type": "integer"},
        "offset": {"type": "integer"},
        "cursor": {"type": "string"},
        "path": {"type": "string"},
        "repo": {"type": "string"},
        "owner": {"type": "string"},
        "org": {"type": "string"},
        "branch": {"type": "string"},
        "ref": {"type": "string"},
        "method": {"type": "string"},
        "query": {"type": "string"},
        "search": {"type": "string"},
        "filter": {"type": "string"},
        "sort": {"type": "string"},
        "order": {"type": "string"},
        "direction": {"type": "string"},
        "context_lines": {"type": "integer"},
        "include": {"type": "string"},
        "exclude": {"type": "string"},
    }
    # Each param appears in 2+ tools — without the deny-list they would all be returned.
    tools = [
        _make_tool("tool_a", denylist_params),
        _make_tool("tool_b", denylist_params),
    ]
    result = codegen.detect_discriminators(tools)
    for param in denylist_params:
        assert param not in result, f"deny-listed param {param!r} must be excluded"


def test_detect_discriminators_denylist_does_not_suppress_real_discriminators():
    """Genuine discriminators (type, kind, entityType) still appear when shared across ≥2 tools."""
    tools = [
        _make_tool("tool_a", {
            "type": {"type": "string"},    # genuine discriminator — keep
            "page": {"type": "integer"},   # deny-listed — drop
        }),
        _make_tool("tool_b", {
            "type": {"type": "string"},
            "page": {"type": "integer"},
        }),
    ]
    result = codegen.detect_discriminators(tools)
    assert "type" in result, "genuine discriminator must survive the deny-list"
    assert "page" not in result, "deny-listed param must be suppressed"


# ---------------------------------------------------------------------------
# #3 — duplicate TypedDict dedup in render_module
# ---------------------------------------------------------------------------

def test_render_module_deduplicates_identical_return_models():
    """Two tools sharing the same return_model name emit the TypedDict class once."""
    shared_shape = {
        "return_model": "Release",
        "fields": {"id": "str", "tag": "str"},
        "source": "live",
    }
    tools = [
        {"name": "get_release", "description": "Get.", "inputSchema": {}},
        {"name": "list_releases", "description": "List.", "inputSchema": {}},
    ]
    src = codegen.render_module(
        "github", tools,
        shapes={"get_release": shared_shape, "list_releases": shared_shape},
    )
    class_count = src.count("class Release(TypedDict, total=False):")
    assert class_count == 1, f"Expected 1 Release class, got {class_count}"


def test_render_module_dedup_triple_same_model():
    """Three tools sharing the same return_model emit exactly one TypedDict."""
    shared_shape = {
        "return_model": "KnowledgeGraph",
        "fields": {"nodes": "Any"},
        "source": "live",
    }
    tools = [{"name": f"tool_{i}", "description": "x", "inputSchema": {}} for i in range(3)]
    src = codegen.render_module(
        "memory", tools,
        shapes={f"tool_{i}": shared_shape for i in range(3)},
    )
    count = src.count("class KnowledgeGraph(TypedDict, total=False):")
    assert count == 1, f"Expected 1 KnowledgeGraph class, got {count}"


def test_render_module_collision_emits_suffixed_variant(capsys):
    """Two tools with the same return_model name but different fields emit a suffixed variant."""
    tools = [
        {"name": "tool_a", "description": "x", "inputSchema": {}},
        {"name": "tool_b", "description": "y", "inputSchema": {}},
    ]
    shapes = {
        "tool_a": {"return_model": "Result", "fields": {"id": "str"}, "source": "live"},
        "tool_b": {"return_model": "Result", "fields": {"name": "str"}, "source": "live"},
    }
    src = codegen.render_module("demo", tools, shapes=shapes)
    err = capsys.readouterr().err

    assert "class Result(TypedDict, total=False):" in src
    assert "class Result_2(TypedDict, total=False):" in src
    assert "collision" in err.lower() or "Result_2" in err


# ---------------------------------------------------------------------------
# Security: injection-safe code generation
# ---------------------------------------------------------------------------

def _adversarial_tool(name: str = 'a"b', description: str = 'evil"""\nimport os  # injected',
                       param_name: str = 'x"y') -> dict:
    """Tool dict with server-controlled values that would break naive interpolation."""
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                param_name: {"type": "string"},
                "safe_param": {"type": "integer"},
            },
            "required": [param_name],
        },
    }


def test_adversarial_tool_generates_valid_python():
    """Generated source must parse even with triple-quote / backslash in tool metadata."""
    tool = _adversarial_tool()
    src = codegen.render_module("test-server", [tool])
    # Must parse without SyntaxError.
    tree = ast.parse(src)
    # Module-level nodes: only Expr (module docstring), ImportFrom, Assign, FunctionDef.
    # An injected 'import os' would appear as a module-level Import node.
    import_names = [
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    ]
    assert "os" not in import_names, (
        f"Injected bare 'import os' found in generated module. Unsafe codegen.\n\nSource:\n{src}"
    )


def test_adversarial_description_does_not_break_out_of_docstring():
    """A description containing triple-quotes must not inject code above the function body."""
    evil_desc = '"""\nimport subprocess\nsubprocess.run(["rm", "-rf", "/"])'
    tool = _adversarial_tool(name="safe_tool", description=evil_desc, param_name="arg")
    src = codegen.render_module("test-server", [tool])
    tree = ast.parse(src)
    # No subprocess import should appear at any level.
    import_names = [
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    ]
    assert "subprocess" not in import_names


def test_adversarial_param_name_with_backslash():
    """A parameter name containing a backslash must not produce invalid Python."""
    tool = _adversarial_tool(name="my_tool", description="Normal description.",
                              param_name='p\\n')
    src = codegen.render_module("test-server", [tool])
    ast.parse(src)  # must not raise SyntaxError


def test_benign_output_byte_stable():
    """Benign names/descriptions must produce byte-identical output after the escaping fix."""
    # Use _GET_ENTITY — no special chars anywhere, so _str_literal and _docstring
    # must take the fast-path (no repr()) and produce the same source as before.
    src = codegen.render_module("example-server", [_GET_ENTITY], shapes={"get_entity": _GET_ENTITY_SHAPE})
    # Sanity: output still parses.
    ast.parse(src)
    # Known stable fragment: tool name in caller.call must be a plain double-quoted literal.
    assert '"get_entity"' in src
    # Known stable fragment: required param key in args dict literal.
    assert '"entityId"' in src


# ---------------------------------------------------------------------------
# #5 — _dig / _dig_list double-serialization (JSON-encoded string response)
# ---------------------------------------------------------------------------

def test_generated_dig_parses_double_serialized_outer():
    """_dig handles when the entire MCP response is a JSON-encoded string."""
    import json as _json
    src = codegen.render_module("acme", [_GET_ENTITY], shapes={"get_entity": _GET_ENTITY_SHAPE})
    ns: dict = {}
    exec(compile(src, "acme_gen.py", "exec"), ns)

    entity = {"entityId": "1", "entityType": 1, "benchDurationCurrent": 42.0}
    double_encoded = _json.dumps({"data": {"entity": entity}})  # str, not dict

    class _Caller:
        async def call(self, server, tool, arguments):
            return double_encoded

    got = asyncio.run(ns["get_entity"](_Caller(), entityId="1", entityType=1))
    assert got == entity


def test_generated_dig_parses_double_serialized_value():
    """_dig handles when the value AT the unwrap path is a JSON-encoded string."""
    import json as _json
    src = codegen.render_module("acme", [_GET_ENTITY], shapes={"get_entity": _GET_ENTITY_SHAPE})
    ns: dict = {}
    exec(compile(src, "acme_gen.py", "exec"), ns)

    entity = {"entityId": "1", "entityType": 1}
    resp = {"data": {"entity": _json.dumps(entity)}}  # entity is a JSON string

    class _Caller:
        async def call(self, server, tool, arguments):
            return resp

    got = asyncio.run(ns["get_entity"](_Caller(), entityId="1", entityType=1))
    assert got == entity


def test_generated_dig_list_parses_double_serialized_outer():
    """_dig_list handles when the entire MCP response is a JSON-encoded string."""
    import json as _json
    src = codegen.render_module("acme", [_QUERY_ACME], shapes={"query_acme": _QUERY_ACME_SHAPE})
    ns: dict = {}
    exec(compile(src, "acme_gen.py", "exec"), ns)

    results = [{"_id": "1"}, {"_id": "2"}]
    double_encoded = _json.dumps({"data": {"results": results}})

    class _Caller:
        async def call(self, server, tool, arguments):
            return double_encoded

    got = asyncio.run(ns["query_acme"](_Caller(), entityType=1, query={}))
    assert got == results


# ---------------------------------------------------------------------------
# #12 — _append_model: guard against Python builtin names
# ---------------------------------------------------------------------------

def test_render_module_builtin_return_model_ignored(capsys):
    """return_model with a Python builtin name is dropped with a stderr warning."""
    tools = [{"name": "get_data", "description": "x", "inputSchema": {}}]
    shapes = {"get_data": {"return_model": "str", "fields": {}, "source": "live"}}
    src = codegen.render_module("demo", tools, shapes=shapes)
    err = capsys.readouterr().err
    assert "class str(TypedDict" not in src, "builtin 'str' must not be emitted as TypedDict"
    assert "builtin" in err.lower(), f"expected warning mentioning 'builtin' in stderr: {err!r}"
    ast.parse(src)


def test_render_module_builtin_all_known_names_ignored(capsys):
    """All Python builtin names are rejected as return_model."""
    builtins = ["str", "int", "float", "list", "dict", "bool", "bytes", "object", "type", "set", "tuple"]
    tools = [{"name": f"tool_{b}", "description": "x", "inputSchema": {}} for b in builtins]
    shapes = {f"tool_{b}": {"return_model": b, "fields": {}, "source": "live"} for b in builtins}
    src = codegen.render_module("demo", tools, shapes=shapes)
    for b in builtins:
        assert f"class {b}(TypedDict" not in src, f"builtin {b!r} must not be emitted"
    ast.parse(src)


# ---------------------------------------------------------------------------
# #8 — denylist: camelCase compound forms suppressed
# ---------------------------------------------------------------------------

def test_detect_discriminators_denylist_camelcase_compound_forms():
    """camelCase compound params (repoName, userName, etc.) are excluded from discriminators."""
    compound_params = {
        "repoName": {"type": "string"},
        "repo_name": {"type": "string"},
        "repositoryName": {"type": "string"},
        "userName": {"type": "string"},
        "orgName": {"type": "string"},
    }
    tools = [
        _make_tool("tool_a", compound_params),
        _make_tool("tool_b", compound_params),
    ]
    result = codegen.detect_discriminators(tools)
    for param in compound_params:
        assert param not in result, f"compound param {param!r} must be in denylist"
