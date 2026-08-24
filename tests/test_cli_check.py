"""Tests for `mcpgen check` — network-free; _list_tools is monkeypatched.

Exit contract under test:
  0 = no drift (advisories allowed)
  1 = drift
  2 = operational/config/auth error
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcpgen import manifest
from mcpgen._bridge import ReauthenticationRequired
from mcpgen.cli import _cmd_check

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


def _write_manifest(tmp_path: Path, tools=TOOLS) -> Path:
    path = tmp_path / "demo.mcpgen.json"
    path.write_text(manifest.dumps(manifest.build("demo", tools)))
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
