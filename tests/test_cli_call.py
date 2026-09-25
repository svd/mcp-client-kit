"""Tests for `mcpgen call` — network-free; _call is monkeypatched via AsyncMock."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from mcpgen import _bridge
from mcpgen.cli import _call, _cmd_call, main

if sys.version_info < (3, 11):  # pragma: no cover - version-gated
    from exceptiongroup import BaseExceptionGroup

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
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "whoami", str(out))

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        rc = _cmd_call(ns)

    assert rc == 0
    assert out.exists()
    assert json.loads(out.read_text()) == FAKE_DICT


def test_call_nothing_on_stdout(tmp_path, capsys):
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "whoami", str(out))

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        _cmd_call(ns)

    assert capsys.readouterr().out == ""


def test_call_stderr_summary_has_size_and_path(tmp_path, capsys):
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "whoami", str(out))

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        _cmd_call(ns)

    err = capsys.readouterr().err
    assert "KB" in err
    assert str(out) in err


# ── string (non-JSON) payload ─────────────────────────────────────────────────


def test_call_string_payload_not_double_encoded(tmp_path):
    out = tmp_path / "srv.probe-raw.json"
    ns = _ns("srv", "ping", str(out))

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_STR):
        _cmd_call(ns)

    assert out.read_text() == FAKE_STR + "\n"


# ── --args forwarded ──────────────────────────────────────────────────────────


def test_call_passes_args_to_underlying_call(tmp_path):
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "get_entity", str(out), args='{"entityId":"x","entityType":1}')

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT) as mock_c:
        _cmd_call(ns)

    mock_c.assert_called_once()
    assert mock_c.call_args.args[2] == {"entityId": "x", "entityType": 1}


def test_call_no_args_defaults_to_empty_dict(tmp_path):
    """--args omitted → args={} passed to _call."""
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "whoami", str(out), args=None)

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT) as mock_c:
        _cmd_call(ns)

    mock_c.assert_called_once()
    assert mock_c.call_args.args[2] == {}


# ── --out required ────────────────────────────────────────────────────────────


def test_call_missing_out_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        main(["call", "acme", "whoami"])  # no --out
    assert exc_info.value.code == 2  # argparse required-arg violation


# ── bad --args JSON ───────────────────────────────────────────────────────────


def test_call_bad_args_json_returns_1(tmp_path, capsys):
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "whoami", str(out), args="{bad json")

    rc = _cmd_call(ns)

    assert rc == 1
    assert not out.exists(), "no file written on parse error"
    err = capsys.readouterr().err
    assert "error" in err.lower() and "--args" in err


# ── PII warning ───────────────────────────────────────────────────────────────


def test_call_emits_pii_warning(tmp_path, capsys):
    out = tmp_path / "acme.probe-raw.json"
    ns = _ns("acme", "whoami", str(out))

    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT):
        _cmd_call(ns)

    err = capsys.readouterr().err
    assert "PII" in err or "probe-raw.json" in err


# ── --timeout and grouped failures ────────────────────────────────────────────


def _grouped(exc: BaseException) -> BaseExceptionGroup:
    """*exc* as it escapes a one-shot session: wrapped by two anyio task groups."""
    inner = BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])


def _timeout_error() -> McpError:
    """The error the SDK raises when a request's read timeout expires."""
    return McpError(
        mcp_types.ErrorData(
            code=408, message="Timed out while waiting for response to ClientRequest. Waited 60.0 seconds."
        )
    )


@pytest.mark.parametrize(("argv_flag", "expected"), [(["--timeout", "5"], 5.0), (["--timeout", "0"], None), ([], 60.0)])
def test_call_timeout_flag_reaches_call(tmp_path, argv_flag, expected):
    """--timeout reaches _call as call_timeout; 0 means no limit (None); absent is 60s."""
    out = tmp_path / "acme.probe-raw.json"
    with patch("mcpgen.cli._call", new_callable=AsyncMock, return_value=FAKE_DICT) as mock_c:
        assert main(["call", "acme", "whoami", "--out", str(out), *argv_flag]) == 0
    assert mock_c.await_args.kwargs["call_timeout"] == expected


@pytest.mark.parametrize("bad", ["-1", "abc", "nan", "inf", "1e14"])
def test_call_timeout_rejects_invalid(tmp_path, bad, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["call", "acme", "whoami", "--out", str(tmp_path / "x.probe-raw.json"), "--timeout", bad])
    assert exc.value.code == 2
    assert "argument --timeout" in capsys.readouterr().err


def test_call_helper_passes_call_timeout_to_the_bridge():
    caller = MagicMock()
    caller.call = AsyncMock(return_value={"ok": True})

    with patch("mcpgen.cli._bridge.McpBridgeCaller", return_value=caller) as bridge:
        asyncio.run(_call("acme", "whoami", {}, call_timeout=7.0))

    assert bridge.call_args.kwargs["call_timeout"] == 7.0


@pytest.mark.parametrize("wrap", [False, True], ids=["bare", "grouped"])
def test_call_timeout_exits_1_with_a_marker_and_writes_nothing(tmp_path, capsys, wrap):
    out = tmp_path / "acme.probe-raw.json"
    error = _grouped(_timeout_error()) if wrap else _timeout_error()

    with patch("mcpgen.cli._call", new_callable=AsyncMock, side_effect=error):
        rc = main(["call", "acme", "annotate", "--out", str(out), "--timeout", "5"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "[call] timeout: no answer within 5s (raise with --timeout)" in err
    assert "Traceback" not in err
    assert not out.exists()


@pytest.mark.parametrize("wrap", [False, True], ids=["bare", "grouped"])
def test_call_tool_mcp_error_exits_1_without_a_traceback(tmp_path, capsys, wrap):
    out = tmp_path / "acme.probe-raw.json"
    tool_error = McpError(mcp_types.ErrorData(code=-32602, message="Unknown tool: nope"))
    error = _grouped(tool_error) if wrap else tool_error

    with patch("mcpgen.cli._call", new_callable=AsyncMock, side_effect=error):
        rc = _cmd_call(_ns("acme", "nope", str(out)))

    assert rc == 1
    assert "[call] error: McpError: Unknown tool: nope" in capsys.readouterr().err
    assert not out.exists()


def test_call_grouped_auth_error_reaches_the_auth_handler(tmp_path, capsys):
    auth = _bridge.ReauthenticationRequired("OAuth re-auth needed for 'acme'. Run: mcpgen login acme")

    with patch("mcpgen.cli._call", new_callable=AsyncMock, side_effect=_grouped(auth)):
        rc = main(["call", "acme", "whoami", "--out", str(tmp_path / "acme.probe-raw.json")])

    assert rc == 1
    assert "[mcpgen] error: OAuth re-auth needed" in capsys.readouterr().err


def test_call_grouped_bug_still_propagates(tmp_path):
    with patch("mcpgen.cli._call", new_callable=AsyncMock, side_effect=_grouped(KeyError("boom"))):
        with pytest.raises(BaseExceptionGroup) as excinfo:
            _cmd_call(_ns("acme", "whoami", str(tmp_path / "acme.probe-raw.json")))

    assert excinfo.group_contains(KeyError)


def test_call_server_sent_408_is_reported_as_an_error_not_a_timeout(tmp_path, capsys):
    """Only the SDK's own read timeout is a timeout; a server's JSON-RPC 408 is its error."""
    out = tmp_path / "acme.probe-raw.json"
    server_408 = McpError(mcp_types.ErrorData(code=408, message="upstream took too long"))

    with patch("mcpgen.cli._call", new_callable=AsyncMock, side_effect=_grouped(server_408)):
        rc = _cmd_call(_ns("acme", "slow", str(out)))

    assert rc == 1
    err = capsys.readouterr().err
    assert "[call] error: McpError: upstream took too long" in err
    assert "timeout:" not in err
