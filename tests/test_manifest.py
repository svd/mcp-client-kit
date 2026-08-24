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


# ── diff ──────────────────────────────────────────────────────────────────────


def _m(tools: list[dict]) -> dict:
    return manifest.build("demo", tools)


BASE = _m(TOOLS)


def test_diff_identical_has_no_drift():
    report = manifest.diff(BASE, _m(TOOLS))
    assert not report.has_drift
    assert report.added == []
    assert report.removed == []
    assert report.changed == []
    assert report.advisory == []


def test_diff_reordered_tools_has_no_drift():
    report = manifest.diff(BASE, _m(list(reversed(TOOLS))))
    assert not report.has_drift


def test_diff_reordered_required_has_no_drift():
    """The canonical-ordering false-positive guard, end to end through diff()."""
    mutated = json.loads(json.dumps(TOOLS))
    mutated[1]["inputSchema"]["required"] = ["b", "a"]
    report = manifest.diff(BASE, _m(mutated))
    assert not report.has_drift


def test_diff_reordered_enum_has_no_drift():
    with_enum = json.loads(json.dumps(TOOLS))
    with_enum[0]["inputSchema"]["properties"]["style"] = {"enum": ["formal", "casual"]}
    left = _m(with_enum)
    with_enum[0]["inputSchema"]["properties"]["style"] = {"enum": ["casual", "formal"]}
    report = manifest.diff(left, _m(with_enum))
    assert not report.has_drift


def test_diff_detects_added_tool():
    mutated = [*TOOLS, {"name": "subtract", "description": "", "inputSchema": {}, "annotations": None}]
    report = manifest.diff(BASE, _m(mutated))
    assert report.added == ["subtract"]
    assert report.has_drift


def test_diff_detects_removed_tool():
    report = manifest.diff(BASE, _m(TOOLS[:1]))
    assert report.removed == ["add"]
    assert report.has_drift


def test_diff_detects_changed_required():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[0]["inputSchema"]["required"] = ["name", "excited"]
    report = manifest.diff(BASE, _m(mutated))
    assert report.has_drift
    assert [c.tool for c in report.changed] == ["greet"]
    assert report.changed[0].category == "required"
    assert "excited" in report.changed[0].detail


def test_diff_detects_changed_enum():
    left = json.loads(json.dumps(TOOLS))
    left[0]["inputSchema"]["properties"]["style"] = {"enum": ["formal", "casual"]}
    right = json.loads(json.dumps(left))
    right[0]["inputSchema"]["properties"]["style"] = {"enum": ["formal", "shouty"]}
    report = manifest.diff(_m(left), _m(right))
    assert report.has_drift
    assert report.changed[0].category == "enum"
    assert "style" in report.changed[0].detail


def test_diff_detects_other_input_schema_change():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[1]["inputSchema"]["properties"]["a"] = {"type": "number"}
    report = manifest.diff(BASE, _m(mutated))
    assert report.changed[0].category == "input_schema"


def test_diff_detects_annotations_change():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[0]["annotations"] = {"readOnlyHint": False}
    report = manifest.diff(BASE, _m(mutated))
    assert report.changed[0].category == "annotations"
    assert report.has_drift


def test_diff_description_change_is_advisory_only():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[1]["description"] = "Adds two integers and returns the sum"
    report = manifest.diff(BASE, _m(mutated))
    assert not report.has_drift
    assert [c.tool for c in report.advisory] == ["add"]
    assert report.advisory[0].category == "description"
    assert report.advisory[0].old == "Add two numbers"
    assert report.advisory[0].new == "Adds two integers and returns the sum"


def test_diff_schema_and_description_change_reports_changed_not_advisory():
    """An advisory must never hide real drift for the same tool."""
    mutated = json.loads(json.dumps(TOOLS))
    mutated[1]["description"] = "different"
    mutated[1]["inputSchema"]["required"] = ["a"]
    report = manifest.diff(BASE, _m(mutated))
    assert [c.tool for c in report.changed] == ["add"]
    assert report.advisory == []
    assert report.has_drift


def test_diff_added_and_removed_are_sorted():
    mutated = [
        {"name": "zeta", "description": "", "inputSchema": {}, "annotations": None},
        {"name": "alpha", "description": "", "inputSchema": {}, "annotations": None},
    ]
    report = manifest.diff(BASE, _m(mutated))
    assert report.added == ["alpha", "zeta"]
    assert report.removed == ["add", "greet"]


def test_diff_rejects_unknown_format_version():
    stale = {"format_version": 99, "server": "demo", "tools": {}}
    with pytest.raises(ValueError, match="format_version"):
        manifest.diff(stale, _m(TOOLS))


def test_diff_rejects_manifest_missing_tools_key():
    with pytest.raises(ValueError):
        manifest.diff({"format_version": 1, "server": "demo"}, _m(TOOLS))


# ── rendering ─────────────────────────────────────────────────────────────────


def test_render_text_clean_report():
    report = manifest.diff(BASE, _m(TOOLS))
    assert manifest.render_text(report) == "No drift."


def test_render_text_clean_report_with_advisory():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[1]["description"] = "different"
    text = manifest.render_text(manifest.diff(BASE, _m(mutated)))
    assert "ADVISORY  demo.add: description changed" in text
    assert text.strip().endswith("No drift. (1 advisory)")


def test_render_text_lists_added_removed_changed():
    mutated = json.loads(json.dumps(TOOLS[:1]))
    mutated[0]["inputSchema"]["required"] = ["name", "excited"]
    mutated.append({"name": "subtract", "description": "", "inputSchema": {}, "annotations": None})
    text = manifest.render_text(manifest.diff(BASE, _m(mutated)))
    assert "ADDED     demo.subtract" in text
    assert "REMOVED   demo.add" in text
    assert "CHANGED   demo.greet: required properties changed:" in text
    assert "Drift detected: 1 added, 1 removed, 1 changed." in text


def test_render_text_summary_counts_advisories_separately():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[0]["inputSchema"]["required"] = ["name", "excited"]
    mutated[1]["description"] = "different"
    text = manifest.render_text(manifest.diff(BASE, _m(mutated)))
    assert "Drift detected: 0 added, 0 removed, 1 changed (1 advisory)." in text


def test_to_json_clean_report():
    payload = manifest.to_json(manifest.diff(BASE, _m(TOOLS)))
    assert payload == {
        "server": "demo",
        "drift": False,
        "added": [],
        "removed": [],
        "changed": [],
        "advisory": [],
    }


def test_to_json_drift_report_is_serializable():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[0]["inputSchema"]["required"] = ["name", "excited"]
    payload = manifest.to_json(manifest.diff(BASE, _m(mutated)))
    assert payload["drift"] is True
    assert payload["changed"][0]["tool"] == "greet"
    assert payload["changed"][0]["category"] == "required"
    assert payload["changed"][0]["new"] == ["excited", "name"]
    json.dumps(payload)  # must not raise


def test_to_json_advisory_does_not_set_drift():
    mutated = json.loads(json.dumps(TOOLS))
    mutated[1]["description"] = "different"
    payload = manifest.to_json(manifest.diff(BASE, _m(mutated)))
    assert payload["drift"] is False
    assert payload["advisory"][0]["category"] == "description"
