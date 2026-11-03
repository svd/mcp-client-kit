"""Tests for manifest emission from `mcpgen codegen` — network-free."""

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

from mcpgen import codegen, manifest
from mcpgen.cli import Inventory, _cmd_codegen, _part_filename, main

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

FAKE_TOOLS = [
    {
        "name": "greet",
        "description": "Greet someone",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "annotations": None,
    },
]


def _ns(tmp_path: Path, out: str | None = "demo.py", **overrides) -> SimpleNamespace:
    ns = SimpleNamespace(
        server="demo",
        out=str(tmp_path / out) if out else None,
        shapes=None,
        probe=None,
        probe_args=None,
        stdio=None,
        embed_schema=False,
        manifest=None,
        no_manifest=False,
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


def test_codegen_writes_manifest_beside_out(tmp_path):
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 0
    written = tmp_path / "demo.mcpgen.json"
    assert written.exists()
    payload = json.loads(written.read_text())
    assert payload["server"] == "demo"
    assert set(payload["tools"]) == {"greet"}


def test_codegen_manifest_derives_from_out_stem_not_server_name(tmp_path):
    """--out gen/wrapper.py must produce gen/wrapper.mcpgen.json, not demo.mcpgen.json."""
    (tmp_path / "gen").mkdir()
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path, out="gen/wrapper.py"))
    assert (tmp_path / "gen" / "wrapper.mcpgen.json").exists()
    assert not (tmp_path / "gen" / "demo.mcpgen.json").exists()


def test_codegen_manifest_is_deterministic(tmp_path):
    """Two runs over the same inventory produce byte-identical manifests."""
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path))
        first = (tmp_path / "demo.mcpgen.json").read_bytes()
        _cmd_codegen(_ns(tmp_path))
        second = (tmp_path / "demo.mcpgen.json").read_bytes()
    assert first == second


def test_codegen_manifest_matches_manifest_build(tmp_path):
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path))
    text = (tmp_path / "demo.mcpgen.json").read_text()
    assert text == manifest.dumps(manifest.build("demo", FAKE_TOOLS))


def test_codegen_manifest_flag_overrides_path(tmp_path):
    target = tmp_path / "custom" / "snapshot.json"
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path, manifest=str(target)))
    assert target.exists()
    assert not (tmp_path / "demo.mcpgen.json").exists()


def test_codegen_no_manifest_flag_suppresses_write(tmp_path):
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path, no_manifest=True))
    assert not (tmp_path / "demo.mcpgen.json").exists()


def test_codegen_stdout_mode_writes_no_manifest(tmp_path, capsys):
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        rc = _cmd_codegen(_ns(tmp_path, out=None))
    assert rc == 0
    assert list(tmp_path.iterdir()) == []
    assert "async def greet" in capsys.readouterr().out


def test_codegen_still_writes_the_python_module(tmp_path):
    """Manifest emission must not disturb the existing artifact."""
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path))
    assert "async def greet" in (tmp_path / "demo.py").read_text()


def test_codegen_reports_manifest_on_stderr(tmp_path, capsys):
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_codegen(_ns(tmp_path))
    assert "demo.mcpgen.json" in capsys.readouterr().err


# ── resources (--resources {all,templates,none}) ──────────────────────────────

RESOURCES = [
    {"uri": "demo://config", "name": "config", "description": "Server config", "mimeType": "application/json"},
]
TEMPLATES = [
    {"uriTemplate": "demo://records/{record_id}", "name": "record", "description": "One record"},
]


def _with_inventory(resources=RESOURCES, templates=TEMPLATES, *, lister=None) -> ExitStack:
    """Patch the listing so the real `_list_inventory` runs its resource hook.

    The fake `_list_tools` awaits `on_session` as the real one does inside its
    session; `_bridge.list_resource_inventory` answers with *resources* and
    *templates*, or is replaced by *lister*.
    """

    async def fake_list_tools(server, *, on_session=None, **kwargs):
        if on_session is not None:
            await on_session(object())
        return FAKE_TOOLS

    stack = ExitStack()
    stack.enter_context(patch("mcpgen.cli._list_tools", fake_list_tools))
    stack.enter_context(
        patch("mcpgen.cli._bridge.list_resource_inventory", lister or AsyncMock(return_value=(resources, templates)))
    )
    return stack


def test_codegen_renders_resource_accessors_and_manifest_sections(tmp_path):
    with _with_inventory():
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 0
    source = (tmp_path / "demo.py").read_text()
    assert "async def greet(" in source
    assert "async def read_demo_config(caller: McpResourceReader)" in source
    assert "async def read_demo_records(caller: McpResourceReader, *, record_id: str)" in source
    written = (tmp_path / "demo.mcpgen.json").read_text()
    assert written == manifest.dumps(manifest.build("demo", FAKE_TOOLS, RESOURCES, TEMPLATES))
    payload = json.loads(written)
    assert set(payload["resources"]) == {"demo://config"}
    assert set(payload["resource_templates"]) == {"demo://records/{record_id}"}


def test_codegen_resources_default_is_all(tmp_path):
    """The flag is read with getattr, so a namespace without it collects everything."""
    ns = _ns(tmp_path)
    assert not hasattr(ns, "resource_scope")
    with _with_inventory():
        _cmd_codegen(ns)
    assert "read_demo_config" in (tmp_path / "demo.py").read_text()


def test_codegen_resources_templates_skips_statics(tmp_path):
    lister = AsyncMock(return_value=([], TEMPLATES))
    with _with_inventory(lister=lister):
        rc = _cmd_codegen(_ns(tmp_path, resource_scope="templates"))
    assert rc == 0
    assert lister.await_args.kwargs == {"statics": False}
    source = (tmp_path / "demo.py").read_text()
    assert "read_demo_config" not in source
    assert "async def read_demo_records(" in source
    payload = json.loads((tmp_path / "demo.mcpgen.json").read_text())
    assert "resources" not in payload
    assert set(payload["resource_templates"]) == {"demo://records/{record_id}"}
    assert payload["resource_scope"] == "templates"


def test_codegen_resources_none_writes_tools_only_module_and_records_scope(tmp_path):
    """The module is tools-only; the manifest records `none` so `check` does not list resources."""
    lister = AsyncMock(return_value=(RESOURCES, TEMPLATES))
    with _with_inventory(lister=lister):
        rc = _cmd_codegen(_ns(tmp_path, resource_scope="none"))
    assert rc == 0
    lister.assert_not_awaited()
    assert (tmp_path / "demo.py").read_text() == codegen.render_module("demo", FAKE_TOOLS)
    written = (tmp_path / "demo.mcpgen.json").read_text()
    assert written == manifest.dumps(manifest.build("demo", FAKE_TOOLS, resource_scope="none"))
    payload = json.loads(written)
    assert payload["resource_scope"] == "none"
    assert "resources" not in payload
    assert "resource_templates" not in payload


def test_codegen_resources_all_records_no_scope(tmp_path):
    with _with_inventory():
        assert _cmd_codegen(_ns(tmp_path, resource_scope="all")) == 0
    assert "resource_scope" not in json.loads((tmp_path / "demo.mcpgen.json").read_text())


def test_codegen_applies_resource_shapes(tmp_path):
    (tmp_path / "demo.shapes.json").write_text(
        json.dumps(
            {
                "demo://config": {
                    "kind": "resource",
                    "unwrap": ["data"],
                    "return_model": "Config",
                    "fields": {"region": "str"},
                },
                "demo://records/{record_id}": {
                    "kind": "resource_template",
                    "return_model": "Record",
                    "fields": {"id": "int"},
                    "input_overrides": {"record_id": "int"},
                },
            }
        )
    )
    with _with_inventory():
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 0
    source = (tmp_path / "demo.py").read_text()
    assert "class Config(TypedDict" in source
    assert "async def read_demo_config(caller: McpResourceReader) -> Config:" in source
    assert "_dig(" in source
    assert "async def read_demo_records(caller: McpResourceReader, *, record_id: int) -> Record:" in source


def test_codegen_applies_resource_shapes_from_parts_fallback(tmp_path):
    """Unmerged probe parts reach render_module through the in-memory merge."""
    parts = tmp_path / "demo.shapes.json.parts"
    parts.mkdir()
    skeleton = codegen.probe_skeleton("demo://config", [{}], [{"region": "str"}], kind="resource")
    skeleton["demo://config"]["return_model"] = "Config"
    (parts / _part_filename("demo://config")).write_text(json.dumps(skeleton))
    with _with_inventory():
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 0
    assert "async def read_demo_config(caller: McpResourceReader) -> Config:" in (tmp_path / "demo.py").read_text()


def test_codegen_warns_above_fifty_static_resources(tmp_path, capsys):
    many = [{"uri": f"demo://item/{i}", "name": f"item{i}"} for i in range(51)]
    with _with_inventory(resources=many, templates=[]):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 0
    err = capsys.readouterr().err
    assert "51 static resources" in err
    assert "--resources templates" in err
    assert "--resources none" in err


def test_codegen_fifty_static_resources_do_not_warn(tmp_path, capsys):
    fifty = [{"uri": f"demo://item/{i}", "name": f"item{i}"} for i in range(50)]
    with _with_inventory(resources=fifty, templates=[]):
        _cmd_codegen(_ns(tmp_path))
    assert "static resources" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "exc",
    [McpError(mcp_types.ErrorData(code=-32603, message="boom")), ValueError("repeated pagination cursor")],
)
def test_codegen_resource_listing_error_exits_one_with_hint(tmp_path, capsys, exc):
    with _with_inventory(lister=AsyncMock(side_effect=exc)):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not list resources" in err
    assert "Traceback" not in err
    assert "--resources none" in err
    assert not (tmp_path / "demo.py").exists()
    assert not (tmp_path / "demo.mcpgen.json").exists()


def test_codegen_resource_transport_failure_exits_one_with_hint(tmp_path, capsys):
    """A broken stream under the hook surfaces as ResourceListingFailed."""
    with _with_inventory(lister=AsyncMock(side_effect=anyio.BrokenResourceError())):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not list resources" in err
    assert "--resources none" in err


def test_codegen_config_error_gets_no_resources_hint(tmp_path, capsys):
    """A ValueError from resolving the server is not a resource failure."""
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=ValueError("not found in config")):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "[codegen] error: not found in config" in err
    assert "--resources none" not in err


def test_codegen_tools_listing_mcp_error_is_described(tmp_path, capsys):
    with patch(
        "mcpgen.cli._list_tools",
        new_callable=AsyncMock,
        side_effect=McpError(mcp_types.ErrorData(code=-32603, message="boom")),
    ):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "[codegen] error: McpError: boom" in err
    assert "--resources none" not in err


def test_codegen_grouped_tools_listing_error_exits_one_without_traceback(tmp_path, capsys):
    """Real transports deliver a tools/list McpError inside an ExceptionGroup."""
    group = ExceptionGroup("x", [McpError(mcp_types.ErrorData(code=-32603, message="boom"))])
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=group):
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 1
    err = capsys.readouterr().err
    assert "[codegen] error: McpError: boom" in err
    assert not (tmp_path / "demo.py").exists()


def test_codegen_omits_zero_resource_count_lines(tmp_path, capsys):
    with _with_inventory(resources=[], templates=[]):
        assert _cmd_codegen(_ns(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "resource(s)" not in err
    assert "resource template(s)" not in err


def test_codegen_prints_resource_count_lines(tmp_path, capsys):
    with _with_inventory():
        assert _cmd_codegen(_ns(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "[codegen] demo: 1 resource(s)" in err
    assert "[codegen] demo: 1 resource template(s)" in err


def test_codegen_warns_on_unknown_shapes_kind(tmp_path, capsys):
    """A misspelled `kind` is applied by nothing, so it must not pass silently."""
    (tmp_path / "demo.shapes.json").write_text(
        json.dumps(
            {
                "demo://records/{record_id}": {"kind": "resource-template", "return_model": "Record"},
                "greet": {"kind": "tool", "return_model": "Greeting"},
            }
        )
    )
    with _with_inventory():
        assert _cmd_codegen(_ns(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "shapes entry 'demo://records/{record_id}' has unknown kind 'resource-template' — not applied" in err
    assert "'greet'" not in err
    assert "-> Record:" not in (tmp_path / "demo.py").read_text()


def test_codegen_warns_on_shapes_entry_matching_no_listed_template(tmp_path, capsys):
    """A probe keyed by a mistyped template variable is flagged, not silently dropped."""
    (tmp_path / "demo.shapes.json").write_text(
        json.dumps({"demo://records/{id}": {"kind": "resource_template", "return_model": "Record"}})
    )
    with _with_inventory():
        rc = _cmd_codegen(_ns(tmp_path))
    assert rc == 0
    err = capsys.readouterr().err
    assert "'demo://records/{id}'" in err
    assert "matches no listed resource template" in err


def test_codegen_warns_on_untagged_resource_entry_matching_nothing(tmp_path, capsys):
    (tmp_path / "demo.shapes.json").write_text(json.dumps({"demo://gone": {"return_model": "Gone"}}))
    with _with_inventory():
        _cmd_codegen(_ns(tmp_path))
    err = capsys.readouterr().err
    assert "'demo://gone'" in err
    assert "matches no listed resource or resource template" in err


def test_codegen_matched_resource_entries_do_not_warn(tmp_path, capsys):
    (tmp_path / "demo.shapes.json").write_text(
        json.dumps(
            {
                "greet": {"return_model": "Greeting"},
                "demo://config": {"kind": "resource", "return_model": "Config"},
                "demo://records/{record_id}": {"return_model": "Record"},
            }
        )
    )
    with _with_inventory():
        _cmd_codegen(_ns(tmp_path))
    assert "matches no listed" not in capsys.readouterr().err


@pytest.mark.parametrize("scope", ["templates", "none"])
def test_codegen_does_not_flag_entries_of_an_uncollected_kind(tmp_path, capsys, scope):
    """Statics are not listed under `templates`/`none`, so a static entry cannot be judged."""
    (tmp_path / "demo.shapes.json").write_text(
        json.dumps(
            {
                "demo://config": {"kind": "resource", "return_model": "Config"},
                "demo://untagged": {"return_model": "Untagged"},
            }
        )
    )
    with _with_inventory(lister=AsyncMock(return_value=([], TEMPLATES))):
        _cmd_codegen(_ns(tmp_path, resource_scope=scope))
    assert "matches no listed" not in capsys.readouterr().err


def test_codegen_resources_flag_passes_scope(tmp_path):
    """`codegen --resources` is a choice with its own dest, apart from `list --resources`."""
    with patch("mcpgen.cli._list_inventory", new_callable=AsyncMock, return_value=Inventory(tools=FAKE_TOOLS)) as inv:
        assert main(["codegen", "demo", "--resources", "templates", "--out", str(tmp_path / "demo.py")]) == 0
        assert inv.await_args.kwargs["scope"] == "templates"
        assert main(["codegen", "demo", "--out", str(tmp_path / "demo.py")]) == 0
        assert inv.await_args.kwargs["scope"] == "all"
    with pytest.raises(SystemExit):
        main(["codegen", "demo", "--resources", "some"])


# ── codegen --probe --timeout ─────────────────────────────────────────────────


def _grouped(exc: BaseException) -> ExceptionGroup:
    """*exc* as it escapes a one-shot session: wrapped by two anyio task groups."""
    return ExceptionGroup("unhandled errors in a TaskGroup", [ExceptionGroup("unhandled errors in a TaskGroup", [exc])])


def _sdk_timeout() -> McpError:
    return McpError(
        mcp_types.ErrorData(
            code=408, message="Timed out while waiting for response to ClientRequest. Waited 5.0 seconds."
        )
    )


@pytest.mark.parametrize(("argv_flag", "expected"), [(["--timeout", "5"], 5.0), (["--timeout", "0"], None), ([], 60.0)])
def test_codegen_probe_timeout_reaches_probe(tmp_path, argv_flag, expected):
    probe = AsyncMock(return_value=({"k": "str"}, 10, {"k": "v"}))
    with (
        patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS),
        patch("mcpgen.cli._probe", probe),
    ):
        rc = main(["codegen", "demo", "--out", str(tmp_path / "demo.py"), "--probe", "greet", *argv_flag])
    assert rc == 0
    assert probe.await_args.kwargs["call_timeout"] == expected


@pytest.mark.parametrize(
    ("error", "marker"),
    [
        (_sdk_timeout(), "[codegen] timeout: no answer within 5s (raise with --timeout)"),
        (
            McpError(mcp_types.ErrorData(code=-32602, message="Unknown tool: greet")),
            "[codegen] error: McpError: Unknown tool: greet",
        ),
    ],
    ids=["timeout", "tool-error"],
)
def test_codegen_probe_failure_exits_1_and_writes_nothing(tmp_path, capsys, error, marker):
    out = tmp_path / "demo.py"
    with (
        patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS),
        patch("mcpgen.cli._probe", new_callable=AsyncMock, side_effect=_grouped(error)),
    ):
        rc = main(["codegen", "demo", "--out", str(out), "--probe", "greet", "--timeout", "5"])
    assert rc == 1
    err = capsys.readouterr().err
    assert marker in err
    assert "Traceback" not in err
    assert not out.exists()
    assert not (tmp_path / "demo.mcpgen.json").exists()


def test_codegen_probe_of_an_is_error_result_exits_1_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "demo.py"
    caller = AsyncMock()
    caller.call_tool_result = AsyncMock(
        return_value=mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="Error executing tool greet: \n  name is required")],
            isError=True,
        )
    )
    with (
        patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS),
        patch("mcpgen.cli._bridge.McpBridgeCaller", return_value=caller),
    ):
        rc = main(["codegen", "demo", "--out", str(out), "--probe", "greet"])
    assert rc == 1
    assert "[codegen] tool returned isError: Error executing tool greet: name is required\n" in capsys.readouterr().err
    assert not out.exists()
    assert not (tmp_path / "demo.mcpgen.json").exists()
