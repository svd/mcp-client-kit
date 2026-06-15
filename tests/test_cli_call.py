"""Tests for `mcp-kit call` — network-free; _call is monkeypatched via AsyncMock."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp_client_kit.cli import _cmd_call, main


# ── helpers ───────────────────────────────────────────────────────────────────

def _ns(server: str, tool: str, out: str, args=None) -> SimpleNamespace:
    return SimpleNamespace(
        server=server,
        tool=tool,
        args=args,
        out=out,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
    )


FAKE_DICT = {"data": {"userId": "u-1234", "name": "Alice"}}
FAKE_STR = "plain text response"


# ── dict payload ──────────────────────────────────────────────────────────────

def test_call_writes_json_payload(tmp_path):
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "whoami", str(out))

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        rc = _cmd_call(ns)

    assert rc == 0
    assert out.exists()
    assert json.loads(out.read_text()) == FAKE_DICT


def test_call_nothing_on_stdout(tmp_path, capsys):
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "whoami", str(out))

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        _cmd_call(ns)

    assert capsys.readouterr().out == ""


def test_call_stderr_summary_has_size_and_path(tmp_path, capsys):
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "whoami", str(out))

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        _cmd_call(ns)

    err = capsys.readouterr().err
    assert "KB" in err
    assert str(out) in err


# ── string (non-JSON) payload ─────────────────────────────────────────────────

def test_call_string_payload_not_double_encoded(tmp_path):
    out = tmp_path / "srv.probe-raw.json"
    ns = _ns("srv", "ping", str(out))

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_STR):
        _cmd_call(ns)

    assert out.read_text() == FAKE_STR + "\n"


# ── --args forwarded ──────────────────────────────────────────────────────────

def test_call_passes_args_to_underlying_call(tmp_path):
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "get_entity", str(out), args='{"entityId":"x","entityType":1}')

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT) as mock_c:
        _cmd_call(ns)

    mock_c.assert_called_once()
    assert mock_c.call_args.args[2] == {"entityId": "x", "entityType": 1}


def test_call_no_args_defaults_to_empty_dict(tmp_path):
    """--args omitted → args={} passed to _call."""
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "whoami", str(out), args=None)

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT) as mock_c:
        _cmd_call(ns)

    mock_c.assert_called_once()
    assert mock_c.call_args.args[2] == {}


# ── --out required ────────────────────────────────────────────────────────────

def test_call_missing_out_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        main(["call", "radar", "whoami"])  # no --out
    assert exc_info.value.code == 2  # argparse required-arg violation


# ── bad --args JSON ───────────────────────────────────────────────────────────

def test_call_bad_args_json_returns_1(tmp_path, capsys):
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "whoami", str(out), args="{bad json")

    rc = _cmd_call(ns)

    assert rc == 1
    assert not out.exists(), "no file written on parse error"
    err = capsys.readouterr().err
    assert "error" in err.lower() and "--args" in err


# ── PII warning ───────────────────────────────────────────────────────────────

def test_call_emits_pii_warning(tmp_path, capsys):
    out = tmp_path / "radar.probe-raw.json"
    ns = _ns("radar", "whoami", str(out))

    with patch("mcp_client_kit.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        _cmd_call(ns)

    err = capsys.readouterr().err
    assert "PII" in err or "probe-raw.json" in err
