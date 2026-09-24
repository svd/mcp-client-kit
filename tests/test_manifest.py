"""Tests for mcpgen.manifest — pure, no I/O, no network."""

from __future__ import annotations

import dataclasses
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


# ── resources ─────────────────────────────────────────────────────────────────

RESOURCES = [
    {
        "uri": "risk://dictionary",
        "name": "risk_dictionary",
        "description": "Read first",
        "mimeType": "application/json",
    },
    {"uri": "dimension://all", "name": "dimensions", "description": "All dimensions", "mimeType": "application/json"},
]
TEMPLATES = [
    {
        "uriTemplate": "dimension://by-unit-type/{unitType}",
        "name": "by_unit_type",
        "description": "Dimensions for one unit type",
        "mimeType": "application/json",
    },
]


def _rm(resources: list[dict] | None = RESOURCES, templates: list[dict] | None = TEMPLATES) -> dict:
    return manifest.build("demo", TOOLS, resources, templates)


def _edit(items: list[dict], index: int, **changes) -> list[dict]:
    copied = json.loads(json.dumps(items))
    copied[index].update(changes)
    return copied


RBASE = _rm()


def test_build_omits_resource_sections_when_not_collected():
    m = manifest.build("demo", TOOLS)
    assert "resources" not in m
    assert "resource_templates" not in m


def test_build_keeps_empty_resource_sections():
    """An empty section is present, meaning the server was asked and tracked."""
    m = manifest.build("demo", TOOLS, [], [])
    assert m["resources"] == {}
    assert m["resource_templates"] == {}


def test_build_sections_are_independent():
    m = manifest.build("demo", TOOLS, RESOURCES, None)
    assert "resources" in m
    assert "resource_templates" not in m


def test_build_keys_resources_by_uri_and_templates_by_uri_template():
    assert set(RBASE["resources"]) == {"risk://dictionary", "dimension://all"}
    assert set(RBASE["resource_templates"]) == {"dimension://by-unit-type/{unitType}"}
    assert RBASE["resources"]["risk://dictionary"] == {
        "name": "risk_dictionary",
        "description": "Read first",
        "mimeType": "application/json",
    }


def test_build_normalizes_missing_resource_fields():
    m = manifest.build("demo", [], [{"uri": "a://b", "name": "b"}], [])
    assert m["resources"]["a://b"] == {"name": "b", "description": "", "mimeType": None}


def test_build_with_resources_is_byte_stable_across_ordering():
    a = manifest.dumps(_rm())
    b = manifest.dumps(manifest.build("demo", TOOLS, list(reversed(RESOURCES)), TEMPLATES))
    assert a == b
    assert manifest.dumps(json.loads(a)) == a


def test_build_duplicate_keys_resolve_independently_of_input_order():
    dupes = [
        {"uri": "a://x", "name": "first", "mimeType": "text/plain"},
        {"uri": "a://x", "name": "second", "mimeType": "application/json"},
    ]
    tpl_dupes = [{"uriTemplate": "t://{id}", "name": "one"}, {"uriTemplate": "t://{id}", "name": "two"}]
    tool_dupes = [
        {"name": "t", "description": "one", "inputSchema": {}},
        {"name": "t", "description": "two", "inputSchema": {}},
    ]
    a = manifest.dumps(manifest.build("demo", tool_dupes, dupes, tpl_dupes))
    b = manifest.dumps(
        manifest.build("demo", list(reversed(tool_dupes)), list(reversed(dupes)), list(reversed(tpl_dupes)))
    )
    assert a == b


def test_diff_identical_resources_has_no_drift():
    report = manifest.diff(RBASE, _rm())
    assert not report.has_drift
    assert report.resources_compared
    assert report.resources_added == []
    assert report.resources_removed == []
    assert report.resources_changed == []
    assert report.resources_advisory == []
    assert report.resources_untracked == []


def test_diff_detects_added_resource():
    extra = [*RESOURCES, {"uri": "status://now", "name": "status", "mimeType": "text/plain"}]
    report = manifest.diff(RBASE, _rm(extra))
    assert report.resources_added == [manifest.ResourceRef("resource", "status://now")]
    assert report.has_drift


def test_diff_detects_removed_resource():
    report = manifest.diff(RBASE, _rm(RESOURCES[:1]))
    assert report.resources_removed == [manifest.ResourceRef("resource", "dimension://all")]
    assert report.has_drift


def test_diff_detects_added_template():
    extra = [*TEMPLATES, {"uriTemplate": "record://{id}", "name": "record"}]
    report = manifest.diff(RBASE, _rm(templates=extra))
    assert report.resources_added == [manifest.ResourceRef("resource_template", "record://{id}")]
    assert report.has_drift


def test_diff_detects_removed_template():
    report = manifest.diff(RBASE, _rm(templates=[]))
    assert report.resources_removed == [
        manifest.ResourceRef("resource_template", "dimension://by-unit-type/{unitType}")
    ]
    assert report.has_drift


def test_diff_mime_type_change_is_drift():
    report = manifest.diff(RBASE, _rm(_edit(RESOURCES, 0, mimeType="text/plain")))
    assert report.has_drift
    assert len(report.resources_changed) == 1
    change = report.resources_changed[0]
    assert (change.kind, change.key, change.category) == ("resource", "risk://dictionary", "mimeType")
    assert (change.old, change.new) == ("application/json", "text/plain")


def test_diff_template_mime_type_change_is_drift():
    report = manifest.diff(RBASE, _rm(templates=_edit(TEMPLATES, 0, mimeType="text/csv")))
    assert report.has_drift
    assert report.resources_changed[0].kind == "resource_template"


def test_diff_resource_description_change_is_advisory():
    report = manifest.diff(RBASE, _rm(_edit(RESOURCES, 0, description="Changed")))
    assert not report.has_drift
    assert [(c.key, c.category) for c in report.resources_advisory] == [("risk://dictionary", "description")]
    assert report.resources_advisory[0].old == "Read first"
    assert report.resources_advisory[0].new == "Changed"


def test_diff_resource_name_change_is_advisory():
    report = manifest.diff(RBASE, _rm(templates=_edit(TEMPLATES, 0, name="renamed")))
    assert not report.has_drift
    assert [(c.kind, c.category) for c in report.resources_advisory] == [("resource_template", "name")]


def test_diff_missing_and_empty_name_or_description_are_equal():
    stored = _rm()
    del stored["resources"]["risk://dictionary"]["description"]
    stored["resource_templates"]["dimension://by-unit-type/{unitType}"]["name"] = None
    live = _rm(_edit(RESOURCES, 0, description=""), _edit(TEMPLATES, 0, name=""))
    report = manifest.diff(stored, live)
    assert report.resources_advisory == []
    assert not report.has_drift


def test_diff_mime_type_comparison_is_case_insensitive():
    """RFC 2045 §5.1: MIME type and subtype names are case-insensitive."""
    report = manifest.diff(RBASE, _rm(_edit(RESOURCES, 0, mimeType="Application/JSON")))
    assert report.resources_changed == []
    assert not report.has_drift


def test_diff_mime_type_appearing_is_drift():
    stored = _rm(_edit(RESOURCES, 0, mimeType=None))
    report = manifest.diff(stored, RBASE)
    assert [(c.key, c.old, c.new) for c in report.resources_changed] == [
        ("risk://dictionary", None, "application/json")
    ]
    assert report.has_drift


def test_diff_mime_type_and_description_change_reports_changed_not_advisory():
    report = manifest.diff(RBASE, _rm(_edit(RESOURCES, 0, mimeType="text/plain", description="x")))
    assert [c.category for c in report.resources_changed] == ["mimeType"]
    assert report.resources_advisory == []


def test_diff_untracked_with_live_resources_is_advisory_not_drift():
    stored = manifest.build("demo", TOOLS)
    report = manifest.diff(stored, _rm())
    assert not report.has_drift
    assert not report.resources_compared
    assert report.resources_untracked == ["resource", "resource_template"]
    assert report.resources_added == []


def test_diff_untracked_with_empty_live_is_silent():
    stored = manifest.build("demo", TOOLS)
    report = manifest.diff(stored, manifest.build("demo", TOOLS, [], []))
    assert not report.has_drift
    assert report.resources_untracked == []
    assert manifest.render_text(report) == "No drift."
    assert "resources" not in manifest.to_json(report)


def test_diff_tracking_is_per_section():
    """Resources tracked, templates untracked: resources compare, templates advise."""
    stored = manifest.build("demo", TOOLS, RESOURCES, None)
    extra = [*RESOURCES, {"uri": "status://now", "name": "status"}]
    report = manifest.diff(stored, _rm(extra))
    assert report.resources_compared
    assert report.resources_added == [manifest.ResourceRef("resource", "status://now")]
    assert report.resources_untracked == ["resource_template"]
    assert report.has_drift


def test_diff_skips_sections_not_collected_live():
    """A live inventory that did not collect resources cannot contradict a tracked manifest."""
    report = manifest.diff(RBASE, manifest.build("demo", TOOLS))
    assert not report.has_drift
    assert not report.resources_compared
    assert report.resources_untracked == []


def test_diff_rejects_non_dict_resource_section():
    bad = {**_rm(), "resources": ["risk://dictionary"]}
    with pytest.raises(ValueError, match="resources"):
        manifest.diff(bad, _rm())
    bad = {**_rm(), "resource_templates": None}
    with pytest.raises(ValueError, match="resource_templates"):
        manifest.diff(_rm(), bad)


@pytest.mark.parametrize("section", ["tools", "resources", "resource_templates"])
def test_diff_rejects_non_dict_entry(section):
    """A hand-edited entry must fail as ValueError, never an AttributeError traceback."""
    bad = _rm()
    bad[section] = {**bad[section], "x://a": "oops"}
    with pytest.raises(ValueError, match=section):
        manifest.diff(bad, _rm())


def test_render_text_resource_lines():
    resources = [*_edit(RESOURCES[:1], 0, mimeType="text/plain", description="x"), {"uri": "status://now", "name": "s"}]
    templates = [*_edit(TEMPLATES, 0, description="new"), {"uriTemplate": "record://{id}", "name": "record"}]
    stored = _rm([*RESOURCES, {"uri": "old://gone", "name": "gone"}])
    text = manifest.render_text(manifest.diff(stored, _rm(resources, templates)))
    assert "ADDED     demo resource status://now" in text
    assert "ADDED     demo resource template record://{id}" in text
    assert "REMOVED   demo resource dimension://all" in text
    assert "REMOVED   demo resource old://gone" in text
    assert "CHANGED   demo resource risk://dictionary: mimeType changed" in text
    assert "ADVISORY  demo resource template dimension://by-unit-type/{unitType}: description changed" in text
    assert "Drift detected: 2 added, 2 removed, 1 changed (1 advisory)." in text


def test_render_text_resource_advisory_shows_old_and_new():
    text = manifest.render_text(manifest.diff(RBASE, _rm(_edit(RESOURCES, 0, description="Changed"))))
    lines = text.splitlines()
    head = lines.index("ADVISORY  demo resource risk://dictionary: description changed")
    assert lines[head + 1] == "            - 'Read first'"
    assert lines[head + 2] == "            + 'Changed'"


def test_render_text_untracked_advisory():
    stored = manifest.build("demo", TOOLS)
    text = manifest.render_text(manifest.diff(stored, _rm(templates=[])))
    assert "ADVISORY  demo: live resources are not tracked by the stored manifest" in text
    assert "re-run with `--update` to track them, or pass `--resources none` to skip them" in text
    assert text.endswith("No drift. (1 advisory)")


def test_resources_error_is_an_advisory():
    stored = manifest.build("demo", TOOLS)
    report = dataclasses.replace(
        manifest.diff(stored, _rm(None, None)), resources_error="could not list resources: McpError: boom"
    )
    assert not report.has_drift
    text = manifest.render_text(report)
    assert "ADVISORY  demo: could not list resources: McpError: boom; the stored manifest tracks no resources" in text
    assert text.endswith("No drift. (1 advisory)")
    block = manifest.to_json(report)["resources"]
    assert block["error"] == "could not list resources: McpError: boom"
    assert block["compared"] is False


def test_render_text_unchanged_without_resources():
    """Tool-only manifests render exactly as before."""
    stored = manifest.build("demo", TOOLS)
    assert manifest.render_text(manifest.diff(stored, _rm(None, None))) == "No drift."


def test_to_json_has_no_resources_block_when_untracked_on_both_sides():
    payload = manifest.to_json(manifest.diff(BASE, _m(TOOLS)))
    assert "resources" not in payload


def test_to_json_resources_block_when_compared():
    extra = [*RESOURCES, {"uri": "status://now", "name": "status"}]
    payload = manifest.to_json(manifest.diff(RBASE, _rm(extra, _edit(TEMPLATES, 0, mimeType="text/csv"))))
    assert payload["drift"] is True
    assert payload["resources"] == {
        "compared": True,
        "untracked": [],
        "added": [{"kind": "resource", "key": "status://now"}],
        "removed": [],
        "changed": [
            {
                "kind": "resource_template",
                "key": "dimension://by-unit-type/{unitType}",
                "category": "mimeType",
                "detail": "mimeType changed: 'application/json' -> 'text/csv'",
                "old": "application/json",
                "new": "text/csv",
            }
        ],
        "advisory": [],
    }
    json.dumps(payload)


def test_to_json_resources_advisory():
    payload = manifest.to_json(manifest.diff(RBASE, _rm(templates=_edit(TEMPLATES, 0, name="renamed"))))
    assert payload["drift"] is False
    assert payload["resources"]["advisory"] == [
        {
            "kind": "resource_template",
            "key": "dimension://by-unit-type/{unitType}",
            "category": "name",
            "detail": "name changed",
            "old": "by_unit_type",
            "new": "renamed",
        }
    ]


def test_to_json_resources_block_when_untracked():
    payload = manifest.to_json(manifest.diff(manifest.build("demo", TOOLS), _rm()))
    assert payload["drift"] is False
    assert payload["resources"]["compared"] is False
    assert payload["resources"]["untracked"] == ["resource", "resource_template"]
