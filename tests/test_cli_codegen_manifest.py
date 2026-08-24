"""Tests for manifest emission from `mcpgen codegen` — network-free."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcpgen import manifest
from mcpgen.cli import _cmd_codegen

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
