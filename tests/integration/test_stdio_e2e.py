"""End-to-end over a real stdio MCP server: codegen → manifest → check.

Unlike the unit suites, nothing is mocked here — a real subprocess, the real
protocol, the real CLI entry points.
"""

from __future__ import annotations

import json

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
    assert set(payload["tools"]) == {"greet", "add", "list_records", "json_payload", "styled"}
    assert "name" in payload["tools"]["greet"]["inputSchema"]["required"]


def test_manifest_is_byte_stable_across_regeneration(tmp_path, stdio_cmd):
    out = tmp_path / "demo.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    first = (tmp_path / "demo.mcpgen.json").read_bytes()
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    assert (tmp_path / "demo.mcpgen.json").read_bytes() == first


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
