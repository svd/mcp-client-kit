"""Tests for mcpgen.manifest — pure, no I/O, no network."""

from __future__ import annotations

import json

import pytest

from mcpgen import manifest

TOOLS = [
    {
        "name": "greet",
        "description": "Greet someone",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "excited": {"type": "boolean"}},
            "required": ["name"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        "annotations": None,
    },
]


def test_build_has_format_version_and_server():
    m = manifest.build("demo", TOOLS)
    assert m["format_version"] == manifest.FORMAT_VERSION
    assert m["server"] == "demo"


def test_build_records_every_tool_by_name():
    m = manifest.build("demo", TOOLS)
    assert set(m["tools"]) == {"greet", "add"}


def test_build_keeps_description_input_schema_and_annotations():
    m = manifest.build("demo", TOOLS)
    entry = m["tools"]["greet"]
    assert entry["description"] == "Greet someone"
    assert entry["inputSchema"]["required"] == ["name"]
    assert entry["annotations"] == {"readOnlyHint": True}


def test_build_normalizes_missing_fields():
    """A tool with no description/annotations gets explicit empty/None, never KeyError."""
    m = manifest.build("demo", [{"name": "bare", "inputSchema": {}}])
    assert m["tools"]["bare"] == {"description": "", "inputSchema": {}, "annotations": None}


def test_build_carries_no_timestamp_or_generator_version():
    """Determinism: the manifest must be a pure function of (server, tools)."""
    m = manifest.build("demo", TOOLS)
    assert set(m) == {"format_version", "server", "tools"}


def test_build_is_deterministic_across_input_ordering():
    """Reordering the tools list must produce a byte-identical manifest."""
    a = manifest.dumps(manifest.build("demo", TOOLS))
    b = manifest.dumps(manifest.build("demo", list(reversed(TOOLS))))
    assert a == b


def test_dumps_is_stable_and_newline_terminated():
    text = manifest.dumps(manifest.build("demo", TOOLS))
    assert text.endswith("\n")
    assert json.loads(text)["server"] == "demo"
    # Re-dumping the parsed form reproduces the same bytes.
    assert manifest.dumps(json.loads(text)) == text


def test_canonical_sorts_nested_object_keys():
    got = manifest.canonical({"b": 1, "a": {"z": 2, "y": 3}})
    assert list(got) == ["a", "b"]
    assert list(got["a"]) == ["y", "z"]


def test_canonical_preserves_positional_array_order():
    """prefixItems/allOf are positional — order is meaningful and must survive."""
    got = manifest.canonical({"allOf": [{"b": 1}, {"a": 2}]})
    assert got["allOf"] == [{"b": 1}, {"a": 2}]


def test_canonical_sorts_required_and_enum():
    """required/enum are sets in JSON Schema semantics — sort so order never reads as drift."""
    got = manifest.canonical({"required": ["b", "a"], "properties": {"s": {"enum": ["z", "y"]}}})
    assert got["required"] == ["a", "b"]
    assert got["properties"]["s"]["enum"] == ["y", "z"]


def test_canonical_sorts_enum_with_mixed_types():
    """enum may hold non-comparable mixed types — sort by JSON repr, never raise."""
    got = manifest.canonical({"enum": [2, "a", None, 1]})
    assert isinstance(got["enum"], list)
    assert len(got["enum"]) == 4


def test_build_applies_canonical_to_input_schema():
    tools = [{"name": "t", "description": "", "inputSchema": {"required": ["b", "a"]}, "annotations": None}]
    m = manifest.build("demo", tools)
    assert m["tools"]["t"]["inputSchema"]["required"] == ["a", "b"]
