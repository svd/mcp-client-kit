"""End-to-end over a real stdio MCP server: codegen → manifest → check.

Unlike the unit suites, nothing is mocked here — a real subprocess, the real
protocol, the real CLI entry points. The fixture server serves resources as
well as tools, so every generated manifest tracks both.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from mcp.shared.exceptions import McpError

from mcpgen import McpBridgeCaller
from mcpgen.cli import main


def test_codegen_produces_importable_module_and_manifest(tmp_path, stdio_cmd):
    out = tmp_path / "demo.py"
    rc = main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    assert rc == 0

    source = out.read_text()
    assert "async def greet" in source
    assert "async def add" in source
    compile(source, str(out), "exec")  # importable

    payload = json.loads((tmp_path / "demo.mcpgen.json").read_text())
    assert payload["server"] == "demo"
    assert set(payload["tools"]) == {"greet", "add", "list_records", "json_payload", "styled", "hang", "refuse"}
    assert "name" in payload["tools"]["greet"]["inputSchema"]["required"]

    assert "async def read_demo_config" in source
    assert "async def read_demo_logo" in source
    assert "async def read_demo_records" in source
    assert payload["resources"] == {
        "demo://config": {"name": "config", "description": "Server configuration.", "mimeType": "application/json"},
        "demo://logo": {
            "name": "logo",
            "description": "The demo logo, as raw bytes.",
            "mimeType": "application/octet-stream",
        },
    }
    assert payload["resource_templates"] == {
        "demo://records/{record_id}": {
            "name": "record",
            "description": "One record by id.",
            "mimeType": "application/json",
        },
    }


def test_manifest_is_byte_stable_across_regeneration(tmp_path, stdio_cmd):
    out = tmp_path / "demo.py"
    manifest_path = tmp_path / "demo.mcpgen.json"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    first = manifest_path.read_bytes()
    # The stability claim covers the resource sections, not just the tools.
    assert {"resources", "resource_templates"} <= set(json.loads(first))

    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    assert manifest_path.read_bytes() == first

    # `check --update`, the other write path, writes the same bytes too.
    assert main(["check", "demo", "--stdio", stdio_cmd, "--manifest", str(manifest_path), "--update"]) == 0
    assert manifest_path.read_bytes() == first


def test_check_against_the_same_server_reports_no_drift(tmp_path, stdio_cmd, capsys):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    capsys.readouterr()

    rc = main(["check", "demo", "--stdio", stdio_cmd, "--manifest", str(tmp_path / "demo.mcpgen.json")])
    assert rc == 0
    assert "No drift." in capsys.readouterr().out


def test_check_against_a_changed_server_reports_drift(tmp_path, stdio_cmd, changed_stdio_cmd, capsys):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    capsys.readouterr()

    rc = main(["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(tmp_path / "demo.mcpgen.json")])
    assert rc == 1

    report = capsys.readouterr().out
    assert "REMOVED   demo.json_payload" in report
    assert "demo.greet: required properties changed: +title" in report
    assert "demo.styled: enum changed for 'style': +shouty" in report


def test_check_json_report_against_a_changed_server(tmp_path, stdio_cmd, changed_stdio_cmd, capsys):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    capsys.readouterr()

    rc = main(
        ["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(tmp_path / "demo.mcpgen.json"), "--json"]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift"] is True
    assert payload["removed"] == ["json_payload"]
    assert {c["tool"] for c in payload["changed"]} == {"greet", "styled"}
    assert payload["resources"]["compared"] is True
    assert payload["resources"]["added"] == [{"kind": "resource", "key": "demo://status"}]
    assert payload["resources"]["removed"] == [{"kind": "resource", "key": "demo://logo"}]
    assert [(c["key"], c["category"]) for c in payload["resources"]["changed"]] == [("demo://config", "mimeType")]


def test_check_does_not_write_the_manifest_on_live_drift(tmp_path, stdio_cmd, changed_stdio_cmd):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    manifest_path = tmp_path / "demo.mcpgen.json"
    before = manifest_path.read_bytes()

    main(["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(manifest_path)])
    assert manifest_path.read_bytes() == before


def test_check_update_accepts_the_changed_server(tmp_path, stdio_cmd, changed_stdio_cmd):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    manifest_path = tmp_path / "demo.mcpgen.json"

    assert main(["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(manifest_path), "--update"]) == 0
    assert main(["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(manifest_path)]) == 0


def test_check_operational_error_on_a_dead_server_exits_two(tmp_path, stdio_cmd):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])

    rc = main(
        [
            "check",
            "demo",
            "--stdio",
            "definitely-not-a-real-command --mcp",
            "--manifest",
            str(tmp_path / "demo.mcpgen.json"),
        ]
    )
    assert rc == 2


def test_list_resources_prints_the_real_inventory(stdio_cmd, capsys):
    assert main(["list", "demo", "--stdio", stdio_cmd, "--resources"]) == 0
    payload = json.loads(capsys.readouterr().out)

    # Only the fields the fixture sets: an SDK may also fill title or size.
    def picked(entry: dict, key: str) -> dict:
        return {k: entry[k] for k in (key, "name", "description", "mimeType")}

    assert [r["uri"] for r in payload["resources"]] == ["demo://config", "demo://logo"]
    assert [t["uriTemplate"] for t in payload["resourceTemplates"]] == ["demo://records/{record_id}"]
    assert [picked(r, "uri") for r in payload["resources"]] == [
        {
            "uri": "demo://config",
            "name": "config",
            "description": "Server configuration.",
            "mimeType": "application/json",
        },
        {
            "uri": "demo://logo",
            "name": "logo",
            "description": "The demo logo, as raw bytes.",
            "mimeType": "application/octet-stream",
        },
    ]
    assert [picked(t, "uriTemplate") for t in payload["resourceTemplates"]] == [
        {
            "uriTemplate": "demo://records/{record_id}",
            "name": "record",
            "description": "One record by id.",
            "mimeType": "application/json",
        },
    ]


def test_check_reports_resource_drift_against_a_changed_server(tmp_path, stdio_cmd, changed_stdio_cmd, capsys):
    out = tmp_path / "demo.py"
    manifest_path = tmp_path / "demo.mcpgen.json"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    capsys.readouterr()

    rc = main(["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(manifest_path)])
    assert rc == 1
    report = capsys.readouterr().out
    assert "ADDED     demo resource demo://status" in report
    assert "REMOVED   demo resource demo://logo" in report
    changed = [line for line in report.splitlines() if line.startswith("CHANGED   demo resource demo://config")]
    assert len(changed) == 1
    assert "mimeType" in changed[0]
    assert "text/plain" in changed[0]
    assert "demo://records/{record_id}" not in report


def test_resource_drift_alone_exits_one(tmp_path, stdio_cmd, changed_stdio_cmd, capsys):
    """With the tool contract already accepted, the resource changes still fail `check`."""
    manifest_path = tmp_path / "demo.mcpgen.json"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(tmp_path / "demo.py")])
    main(["codegen", "demo", "--stdio", changed_stdio_cmd, "--out", str(tmp_path / "changed.py")])
    stored = json.loads(manifest_path.read_text())
    stored["tools"] = json.loads((tmp_path / "changed.mcpgen.json").read_text())["tools"]
    manifest_path.write_text(json.dumps(stored))
    capsys.readouterr()

    rc = main(["check", "demo", "--stdio", changed_stdio_cmd, "--manifest", str(manifest_path), "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert (payload["added"], payload["removed"], payload["changed"]) == ([], [], [])
    assert payload["drift"] is True
    assert payload["resources"]["added"] == [{"kind": "resource", "key": "demo://status"}]
    assert payload["resources"]["removed"] == [{"kind": "resource", "key": "demo://logo"}]


def test_read_of_an_unknown_resource_exits_one_without_a_traceback(tmp_path, stdio_cmd, capsys):
    target = tmp_path / "missing.probe-raw.json"
    rc = main(["read", "demo", "demo://nope", "--stdio", stdio_cmd, "--out", str(target)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "[read] error: McpError:" in err
    assert "demo://nope" in err
    assert not target.exists()
    # The CLI flattens an ExceptionGroup into the same message, so this test cannot
    # tell a bare McpError from a wrapped one; the bridge test below guards that.


def test_unknown_resource_error_escapes_a_one_shot_session_bare(stdio_cmd):
    """anyio must not wrap the server's error in an ExceptionGroup on the read path.

    `pytest.raises(McpError)` does not match a group, so a wrapped error fails here.
    """
    caller = McpBridgeCaller(cmd=stdio_cmd)
    with pytest.raises(McpError, match="demo://nope"):
        asyncio.run(caller.read_resource("demo", "demo://nope"))


def test_probe_of_a_tool_that_never_answers_times_out(tmp_path, stdio_cmd, capsys):
    """--timeout bounds the tool call; the session still closes and reaps the server."""
    shapes_file = tmp_path / "demo.shapes.json"
    started = time.monotonic()
    rc = main(["probe", "demo", "hang", "--stdio", stdio_cmd, "--timeout", "1", "--emit-shape", str(shapes_file)])
    elapsed = time.monotonic() - started

    assert rc == 1
    assert "[probe] timeout: no answer within 1s (raise with --timeout)" in capsys.readouterr().err
    assert not (tmp_path / "demo.shapes.json.parts").exists()
    # 1s of timeout plus server start-up and the SDK's stdio shutdown, with slack for CI.
    assert elapsed < 20


def test_probe_of_a_tool_reporting_is_error_records_no_shape(tmp_path, stdio_cmd, capsys):
    shapes_file = tmp_path / "demo.shapes.json"
    rc = main(["probe", "demo", "refuse", "--stdio", stdio_cmd, "--emit-shape", str(shapes_file)])

    assert rc == 1
    assert (
        '[probe] tool returned isError: Error executing tool refuse: {"code": "validation_error", '
        '"message": "This feature requires a paid plan."}\n'
    ) in capsys.readouterr().err
    assert not (tmp_path / "demo.shapes.json.parts").exists()
    assert not shapes_file.exists()


def test_call_of_a_tool_reporting_is_error_writes_the_body_and_exits_1(tmp_path, stdio_cmd, capsys):
    out = tmp_path / "demo.probe-raw.json"
    rc = main(["call", "demo", "refuse", "--stdio", stdio_cmd, "--out", str(out)])

    assert rc == 1
    assert out.read_text() == (
        'Error executing tool refuse: {"code": "validation_error", "message": "This feature requires a paid plan."}\n'
    )
    assert "[call] tool returned isError: Error executing tool refuse:" in capsys.readouterr().err


def test_a_generated_wrapper_still_returns_an_is_error_body(stdio_cmd):
    """The seam contract is unchanged: ``call()`` parses an isError result like an answer."""
    text = asyncio.run(McpBridgeCaller(cmd=stdio_cmd).call("demo", "refuse", {}))
    assert text.startswith("Error executing tool refuse:")


def test_old_manifest_without_resource_sections_is_advised_then_tracked(tmp_path, stdio_cmd, capsys):
    """A manifest written before resources were tracked: advisory only, until --update tracks them."""
    manifest_path = tmp_path / "demo.mcpgen.json"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(tmp_path / "demo.py")])
    stored = json.loads(manifest_path.read_text())
    del stored["resources"], stored["resource_templates"]
    manifest_path.write_text(json.dumps(stored))
    capsys.readouterr()

    assert main(["check", "demo", "--stdio", stdio_cmd, "--manifest", str(manifest_path)]) == 0
    report = capsys.readouterr().out
    assert "are not tracked by the stored manifest" in report
    assert "--update" in report
    assert "No drift." in report

    assert main(["check", "demo", "--stdio", stdio_cmd, "--manifest", str(manifest_path), "--update"]) == 0
    updated = json.loads(manifest_path.read_text())
    assert set(updated["resources"]) == {"demo://config", "demo://logo"}
    assert set(updated["resource_templates"]) == {"demo://records/{record_id}"}
    capsys.readouterr()

    assert main(["check", "demo", "--stdio", stdio_cmd, "--manifest", str(manifest_path)]) == 0
    report = capsys.readouterr().out
    assert "not tracked" not in report
    assert report.strip() == "No drift."
