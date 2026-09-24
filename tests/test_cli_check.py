"""Tests for `mcpgen check` — network-free; _list_tools is monkeypatched.

Exit contract under test:
  0 = no drift (advisories allowed)
  1 = drift
  2 = operational/config/auth error
"""

from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from mcpgen import manifest
from mcpgen._bridge import ReauthenticationRequired
from mcpgen.cli import _cmd_check, main

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

TOOLS = [
    {
        "name": "greet",
        "description": "Greet someone",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "style": {"enum": ["formal", "casual"]}},
            "required": ["name"],
        },
        "annotations": None,
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


def _write_manifest(tmp_path: Path, tools=TOOLS, resources=None, templates=None) -> Path:
    path = tmp_path / "demo.mcpgen.json"
    path.write_text(manifest.dumps(manifest.build("demo", tools, resources, templates)))
    return path


def _ns(path: Path, **overrides) -> SimpleNamespace:
    ns = SimpleNamespace(
        server="demo",
        manifest=str(path),
        json=False,
        update=False,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
        creds=None,
        env=None,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _mutate(fn) -> list[dict]:
    tools = json.loads(json.dumps(TOOLS))
    fn(tools)
    return tools


# ── exit 0: no drift ──────────────────────────────────────────────────────────


def test_check_no_drift_exits_zero(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        rc = _cmd_check(_ns(path))
    assert rc == 0
    assert "No drift." in capsys.readouterr().out


def test_check_reordered_keys_is_not_drift(tmp_path):
    """Canonical ordering guard: reordered required/enum/object keys must exit 0."""
    path = _write_manifest(tmp_path)
    shuffled = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["b", "a"]))
    shuffled[0]["inputSchema"]["properties"]["style"]["enum"] = ["casual", "formal"]
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=shuffled):
        rc = _cmd_check(_ns(path))
    assert rc == 0


def test_check_reordered_tool_list_is_not_drift(tmp_path):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=list(reversed(TOOLS))):
        rc = _cmd_check(_ns(path))
    assert rc == 0


def test_check_description_change_is_advisory_exit_zero(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[1].__setitem__("description", "Adds two integers"))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ADVISORY" in out
    assert "No drift. (1 advisory)" in out


# ── exit 1: drift ─────────────────────────────────────────────────────────────


def test_check_added_tool_exits_one(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = [*TOOLS, {"name": "subtract", "description": "", "inputSchema": {}, "annotations": None}]
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path))
    assert rc == 1
    assert "ADDED     demo.subtract" in capsys.readouterr().out


def test_check_removed_tool_exits_one(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS[:1]):
        rc = _cmd_check(_ns(path))
    assert rc == 1
    assert "REMOVED   demo.add" in capsys.readouterr().out


def test_check_changed_required_exits_one(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a", "b", "precision"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path))
    assert rc == 1
    out = capsys.readouterr().out
    assert "CHANGED   demo.add: required properties changed: +precision" in out


def test_check_changed_enum_exits_one(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[0]["inputSchema"]["properties"]["style"].__setitem__("enum", ["formal", "shouty"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path))
    assert rc == 1
    assert "enum changed for 'style'" in capsys.readouterr().out


# ── exit 2: operational ───────────────────────────────────────────────────────


def test_check_missing_manifest_exits_two(tmp_path, capsys):
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        rc = _cmd_check(_ns(tmp_path / "absent.mcpgen.json"))
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_check_unparseable_manifest_exits_two(tmp_path):
    path = tmp_path / "demo.mcpgen.json"
    path.write_text("{not json")
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        assert _cmd_check(_ns(path)) == 2


def test_check_unknown_format_version_exits_two(tmp_path):
    path = tmp_path / "demo.mcpgen.json"
    path.write_text(json.dumps({"format_version": 99, "server": "demo", "tools": {}}))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        assert _cmd_check(_ns(path)) == 2


def test_check_transport_failure_exits_two_not_one(tmp_path, capsys):
    """A broken connection must never be reported as schema drift."""
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
        rc = _cmd_check(_ns(path))
    assert rc == 2
    out, err = capsys.readouterr()
    assert "DRIFT" not in out.upper()
    assert "refused" in err


def test_check_auth_failure_exits_two(tmp_path):
    path = _write_manifest(tmp_path)
    with patch(
        "mcpgen.cli._list_tools",
        new_callable=AsyncMock,
        side_effect=ReauthenticationRequired("OAuth re-auth needed"),
    ):
        assert _cmd_check(_ns(path)) == 2


def test_check_unknown_server_exits_two(tmp_path):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=ValueError("not found in config")):
        assert _cmd_check(_ns(path)) == 2


def test_check_missing_config_exits_two(tmp_path):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=FileNotFoundError("config not found")):
        assert _cmd_check(_ns(path)) == 2


# ── no probing ────────────────────────────────────────────────────────────────


def test_check_never_calls_a_tool(tmp_path):
    """`check` must not probe: no call_tool, no session beyond tools/list."""
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._probe", new_callable=AsyncMock) as probe:
        with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
            _cmd_check(_ns(path))
    probe.assert_not_called()


def test_check_does_not_read_or_write_shapes(tmp_path):
    """`check` must leave the shape-spec sidecar entirely alone."""
    path = _write_manifest(tmp_path)
    shapes = tmp_path / "demo.shapes.json"
    shapes.write_text('{"greet": {"return_model": "Greeting"}}')
    before = shapes.read_bytes()
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        _cmd_check(_ns(path))
    assert shapes.read_bytes() == before


# ── manifest is never written without --update ────────────────────────────────


def test_check_does_not_write_manifest_when_clean(tmp_path):
    path = _write_manifest(tmp_path)
    before = path.read_bytes()
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        _cmd_check(_ns(path))
    assert path.read_bytes() == before


def test_check_does_not_write_manifest_on_drift_without_update(tmp_path):
    """Silent write is the one thing check must never do."""
    path = _write_manifest(tmp_path)
    before = path.read_bytes()
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        assert _cmd_check(_ns(path)) == 1
    assert path.read_bytes() == before


# ── --json ────────────────────────────────────────────────────────────────────


def test_check_json_clean_report(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        rc = _cmd_check(_ns(path, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"server": "demo", "drift": False, "added": [], "removed": [], "changed": [], "advisory": []}


def test_check_json_drift_report(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a", "b", "precision"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path, json=True))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift"] is True
    assert payload["changed"][0]["tool"] == "add"
    assert payload["changed"][0]["category"] == "required"


def test_check_json_error_report_is_parseable(tmp_path, capsys):
    """A CI job parsing stdout must not choke on the exit-2 path."""
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
        rc = _cmd_check(_ns(path, json=True))
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["server"] == "demo"
    assert "refused" in payload["error"]
    assert "drift" not in payload


def test_check_json_emits_no_human_text(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        _cmd_check(_ns(path, json=True))
    assert "No drift." not in capsys.readouterr().out


# ── --update ──────────────────────────────────────────────────────────────────


def test_check_update_rewrites_manifest_and_exits_zero(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a", "b", "precision"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path, update=True))
    assert rc == 0
    assert json.loads(path.read_text())["tools"]["add"]["inputSchema"]["required"] == ["a", "b", "precision"]
    assert "updated" in capsys.readouterr().out


def test_check_update_is_idempotent(tmp_path):
    """After --update, a plain check on the same inventory is clean."""
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a", "b", "precision"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        _cmd_check(_ns(path, update=True))
        assert _cmd_check(_ns(path)) == 0


def test_check_update_writes_nothing_on_operational_error(tmp_path):
    path = _write_manifest(tmp_path)
    before = path.read_bytes()
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
        assert _cmd_check(_ns(path, update=True)) == 2
    assert path.read_bytes() == before


def test_check_update_on_clean_inventory_leaves_bytes_identical(tmp_path):
    path = _write_manifest(tmp_path)
    before = path.read_bytes()
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        assert _cmd_check(_ns(path, update=True)) == 0
    assert path.read_bytes() == before


def test_check_update_creates_manifest_when_absent(tmp_path):
    """--update is the documented way to bootstrap a manifest for an existing wrapper."""
    path = tmp_path / "demo.mcpgen.json"
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=TOOLS):
        rc = _cmd_check(_ns(path, update=True))
    assert rc == 0
    assert json.loads(path.read_text())["server"] == "demo"


def test_check_update_json_reports_update(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        rc = _cmd_check(_ns(path, update=True, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] is True
    assert payload["drift"] is True


# ── resources ─────────────────────────────────────────────────────────────────

RESOURCES = [{"uri": "demo://config", "name": "config", "description": "Config", "mimeType": "application/json"}]
TEMPLATES = [{"uriTemplate": "demo://records/{record_id}", "name": "record"}]
STATUS = {"uri": "demo://status", "name": "status", "mimeType": "text/plain"}


def _with_inventory(resources=RESOURCES, templates=TEMPLATES, *, lister=None, preview=None) -> ExitStack:
    """Patch the listing so the real `_list_inventory` runs its resource hook.

    The fake `_list_tools` awaits `on_session` as the real one does inside its
    session. The strict `_bridge.list_resource_inventory` and the first-page
    `_bridge.preview_resource_inventory` answer with *resources* and
    *templates* (no further pages), or are replaced by *lister* / *preview*.
    """

    async def fake_list_tools(server, *, on_session=None, **kwargs):
        if on_session is not None:
            await on_session(object())
        return TOOLS

    stack = ExitStack()
    stack.enter_context(patch("mcpgen.cli._list_tools", fake_list_tools))
    stack.enter_context(
        patch("mcpgen.cli._bridge.list_resource_inventory", lister or AsyncMock(return_value=(resources, templates)))
    )
    stack.enter_context(
        patch(
            "mcpgen.cli._bridge.preview_resource_inventory",
            preview or AsyncMock(return_value=(resources, False, templates, False)),
        )
    )
    return stack


def test_check_lists_static_resources_and_templates(tmp_path):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    lister = AsyncMock(return_value=(RESOURCES, TEMPLATES))
    with _with_inventory(lister=lister):
        assert _cmd_check(_ns(path)) == 0
    assert lister.await_args.kwargs == {"statics": True}


def test_check_resource_added_exits_one(tmp_path, capsys):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    with _with_inventory(resources=[*RESOURCES, STATUS]):
        rc = _cmd_check(_ns(path))
    assert rc == 1
    assert "ADDED     demo resource demo://status" in capsys.readouterr().out


def test_check_old_manifest_with_live_resources_is_advisory_exit_zero(tmp_path, capsys):
    """A manifest from before resource tracking gets an advisory, not drift."""
    path = _write_manifest(tmp_path)
    before = path.read_bytes()
    with _with_inventory():
        rc = _cmd_check(_ns(path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "live resources are not tracked by the stored manifest" in out
    assert "live resource templates are not tracked" in out
    assert "re-run with `--update` to track them, or pass `--resources none` to skip them" in out
    assert "No drift. (2 advisory)" in out
    assert path.read_bytes() == before


def test_check_update_does_not_advise_tracking_it_just_started(tmp_path, capsys):
    """The run that writes the sections reports them as tracked, not as untracked."""
    path = _write_manifest(tmp_path)
    with _with_inventory():
        rc = _cmd_check(_ns(path, update=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "not tracked" not in out
    assert "No drift.\n" in out
    assert "now tracking resources and resource templates" in out

    path = _write_manifest(tmp_path)
    with _with_inventory():
        rc = _cmd_check(_ns(path, update=True, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] is True
    # Nothing was compared and nothing is untracked any more, so there is no block.
    assert "resources" not in payload


def test_check_update_adds_resource_sections(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    with _with_inventory():
        rc = _cmd_check(_ns(path, update=True))
    assert rc == 0
    assert path.read_text() == manifest.dumps(manifest.build("demo", TOOLS, RESOURCES, TEMPLATES))
    assert "(accepted 0 change(s); now tracking resources and resource templates)" in capsys.readouterr().out
    with _with_inventory():
        assert _cmd_check(_ns(path)) == 0
    assert "No drift." in capsys.readouterr().out


def test_check_update_counts_resource_changes(tmp_path, capsys):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    with _with_inventory(resources=[STATUS]):
        assert _cmd_check(_ns(path, update=True)) == 0
    assert "(accepted 2 change(s))" in capsys.readouterr().out
    assert set(json.loads(path.read_text())["resources"]) == {"demo://status"}


def test_check_update_keeps_stored_sections_when_resources_not_collected(tmp_path):
    """A live inventory without resources must not erase the tracked sections."""
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    live = _mutate(lambda t: t[1]["inputSchema"].__setitem__("required", ["a"]))
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=live):
        assert _cmd_check(_ns(path, update=True)) == 0
    written = json.loads(path.read_text())
    assert written["tools"]["add"]["inputSchema"]["required"] == ["a"]
    stored = manifest.build("demo", TOOLS, RESOURCES, TEMPLATES)
    assert written["resources"] == stored["resources"]
    assert written["resource_templates"] == stored["resource_templates"]


def test_check_json_resources_block(tmp_path, capsys):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    with _with_inventory(resources=[*RESOURCES, STATUS]):
        rc = _cmd_check(_ns(path, json=True))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift"] is True
    assert payload["added"] == []
    assert payload["resources"] == {
        "compared": True,
        "untracked": [],
        "added": [{"kind": "resource", "key": "demo://status"}],
        "removed": [],
        "changed": [],
        "advisory": [],
    }


@pytest.mark.parametrize(
    "exc",
    [
        McpError(mcp_types.ErrorData(code=-32603, message="boom")),
        ValueError("repeated pagination cursor"),
        anyio.BrokenResourceError(),
    ],
)
def test_check_resource_listing_failure_exits_two(tmp_path, capsys, exc):
    """A failed resource listing is operational, never drift, and never a traceback."""
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    before = path.read_bytes()
    with _with_inventory(lister=AsyncMock(side_effect=exc)):
        rc = _cmd_check(_ns(path, update=True, json=True))
    assert rc == 2
    out, err = capsys.readouterr()
    assert "could not list resources" in json.loads(out)["error"]
    assert "Traceback" not in err
    assert path.read_bytes() == before


def test_check_mime_type_change_exits_one(tmp_path, capsys):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    live = [{**RESOURCES[0], "mimeType": "text/plain"}]
    with _with_inventory(resources=live):
        assert _cmd_check(_ns(path)) == 1
    assert "CHANGED   demo resource demo://config: mimeType changed" in capsys.readouterr().out


def test_check_resource_name_and_description_change_is_advisory(tmp_path, capsys):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    live = [{**RESOURCES[0], "name": "configuration", "description": "Server settings"}]
    with _with_inventory(resources=live):
        assert _cmd_check(_ns(path)) == 0
    out = capsys.readouterr().out
    assert "ADVISORY  demo resource demo://config: name changed" in out
    assert "ADVISORY  demo resource demo://config: description changed" in out
    assert "No drift. (2 advisory)" in out


def test_check_update_without_server_resources_writes_empty_sections(tmp_path):
    path = _write_manifest(tmp_path)
    with _with_inventory(resources=[], templates=[]):
        assert _cmd_check(_ns(path, update=True)) == 0
        first = path.read_bytes()
        assert _cmd_check(_ns(path, update=True)) == 0
    written = json.loads(first)
    assert written["resources"] == {}
    assert written["resource_templates"] == {}
    assert path.read_bytes() == first


def test_check_update_with_resources_is_byte_identical_on_rerun(tmp_path):
    path = _write_manifest(tmp_path)
    with _with_inventory():
        _cmd_check(_ns(path, update=True))
        first = path.read_bytes()
        assert _cmd_check(_ns(path, update=True)) == 0
    assert path.read_bytes() == first


# ── resource scope: derived from the stored manifest, or --resources ──────────


def test_check_templates_only_manifest_skips_static_listing(tmp_path, capsys):
    """A manifest from `codegen --resources templates` is checked without resources/list."""
    path = _write_manifest(tmp_path, templates=TEMPLATES)
    lister = AsyncMock(return_value=([], TEMPLATES))
    with _with_inventory(lister=lister):
        assert _cmd_check(_ns(path, update=True)) == 0
    assert lister.await_args.kwargs == {"statics": False}
    written = json.loads(path.read_text())
    assert "resources" not in written
    assert set(written["resource_templates"]) == set(
        manifest.build("demo", TOOLS, None, TEMPLATES)["resource_templates"]
    )
    assert "now tracking" not in capsys.readouterr().out


def test_check_untracked_manifest_previews_first_page_only(tmp_path):
    """Only the untracked advisory needs the listing, so no page beyond the first is read."""
    path = _write_manifest(tmp_path)
    lister = AsyncMock(return_value=(RESOURCES, TEMPLATES))
    preview = AsyncMock(return_value=(RESOURCES, True, TEMPLATES, False))
    with _with_inventory(lister=lister, preview=preview):
        assert _cmd_check(_ns(path)) == 0
    lister.assert_not_awaited()
    assert preview.await_args.kwargs == {"statics": True}


def test_check_untracked_truncated_empty_first_page_is_still_advisory(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    with _with_inventory(preview=AsyncMock(return_value=([], True, [], False))):
        assert _cmd_check(_ns(path)) == 0
    out = capsys.readouterr().out
    assert "live resources are not tracked by the stored manifest" in out
    assert "resource templates are not tracked" not in out
    assert "No drift. (1 advisory)" in out


def test_check_untracked_update_lists_strictly(tmp_path):
    """--update writes the sections, so it must read every page."""
    path = _write_manifest(tmp_path)
    lister = AsyncMock(return_value=(RESOURCES, TEMPLATES))
    preview = AsyncMock(return_value=(RESOURCES, True, TEMPLATES, False))
    with _with_inventory(lister=lister, preview=preview):
        assert _cmd_check(_ns(path, update=True)) == 0
    preview.assert_not_awaited()
    assert lister.await_args.kwargs == {"statics": True}


def test_check_explicit_scope_none_overrides_and_update_keeps_sections(tmp_path, capsys):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    before = json.loads(path.read_text())
    lister = AsyncMock(return_value=([STATUS], []))
    with _with_inventory(lister=lister):
        assert _cmd_check(_ns(path, resource_scope="none", update=True)) == 0
    lister.assert_not_awaited()
    written = json.loads(path.read_text())
    assert written["resources"] == before["resources"]
    assert written["resource_templates"] == before["resource_templates"]


def test_check_explicit_scope_templates_keeps_stored_statics(tmp_path):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    before = json.loads(path.read_text())
    lister = AsyncMock(return_value=([], []))
    with _with_inventory(lister=lister):
        assert _cmd_check(_ns(path, resource_scope="templates", update=True)) == 0
    assert lister.await_args.kwargs == {"statics": False}
    written = json.loads(path.read_text())
    assert written["resources"] == before["resources"]
    assert written["resource_templates"] == {}


def test_check_untracked_listing_failure_is_advisory(tmp_path, capsys):
    """With nothing stored to compare, a failed resources/list must not fail `check`."""
    path = _write_manifest(tmp_path)
    exc = McpError(mcp_types.ErrorData(code=-32603, message="boom"))
    with _with_inventory(preview=AsyncMock(side_effect=exc)):
        rc = _cmd_check(_ns(path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ADVISORY  demo: could not list resources: McpError: boom" in out
    assert "No drift. (1 advisory)" in out


def test_check_untracked_listing_failure_json(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    exc = McpError(mcp_types.ErrorData(code=-32603, message="boom"))
    with _with_inventory(preview=AsyncMock(side_effect=exc)):
        rc = _cmd_check(_ns(path, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift"] is False
    assert payload["resources"]["error"] == "could not list resources: McpError: boom"


def test_check_untracked_transport_failure_recovers_tools(tmp_path, capsys):
    """A broken session under the preview is re-listed for tools; the failure is an advisory."""
    path = _write_manifest(tmp_path)
    live = [*TOOLS, {"name": "subtract", "description": "", "inputSchema": {}, "annotations": None}]
    calls = []

    async def fake_list_tools(server, *, on_session=None, **kwargs):
        calls.append(on_session)
        if on_session is not None:
            await on_session(object())
        return live

    with (
        patch("mcpgen.cli._list_tools", fake_list_tools),
        patch("mcpgen.cli._bridge.preview_resource_inventory", AsyncMock(side_effect=anyio.BrokenResourceError())),
    ):
        rc = _cmd_check(_ns(path))
    assert rc == 1
    assert len(calls) == 2
    assert calls[1] is None
    out = capsys.readouterr().out
    assert "ADDED     demo.subtract" in out
    assert "ADVISORY  demo: could not list resources: BrokenResourceError" in out


def test_check_untracked_listing_failure_with_update_exits_two(tmp_path, capsys):
    """--update lists strictly, so a failure is an error, never a reported success."""
    path = _write_manifest(tmp_path)
    before = path.read_bytes()
    exc = McpError(mcp_types.ErrorData(code=-32603, message="boom"))
    with _with_inventory(lister=AsyncMock(side_effect=exc)):
        rc = _cmd_check(_ns(path, json=True, update=True))
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"] == "could not list resources: McpError: boom"
    assert path.read_bytes() == before


def test_check_templates_only_manifest_listing_failure_exits_two(tmp_path, capsys):
    path = _write_manifest(tmp_path, templates=TEMPLATES)
    exc = McpError(mcp_types.ErrorData(code=-32603, message="boom"))
    with _with_inventory(lister=AsyncMock(side_effect=exc)):
        rc = _cmd_check(_ns(path))
    assert rc == 2
    assert "could not list resources: McpError: boom" in capsys.readouterr().err


def test_check_untracked_listing_failure_with_explicit_all_exits_two(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    exc = McpError(mcp_types.ErrorData(code=-32603, message="boom"))
    with _with_inventory(lister=AsyncMock(side_effect=exc)):
        rc = _cmd_check(_ns(path, resource_scope="all"))
    assert rc == 2
    assert "could not list resources: McpError: boom" in capsys.readouterr().err


def test_check_grouped_error_is_described(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    group = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("refused")])
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=group):
        assert _cmd_check(_ns(path)) == 2
    err = capsys.readouterr().err
    assert "[check] error: ConnectionError: refused" in err
    assert "unhandled errors" not in err


def test_check_resources_flag_parses(tmp_path):
    path = _write_manifest(tmp_path, resources=RESOURCES, templates=TEMPLATES)
    lister = AsyncMock(return_value=(RESOURCES, TEMPLATES))
    with _with_inventory(lister=lister):
        assert main(["check", "demo", "--manifest", str(path), "--resources", "templates"]) == 0
    assert lister.await_args.kwargs == {"statics": False}
    with pytest.raises(SystemExit):
        main(["check", "demo", "--resources", "some"])
