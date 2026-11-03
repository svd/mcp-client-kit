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
    caller.call_tool_result = AsyncMock(
        return_value=mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text='{"ok": true}')])
    )

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


# ── a result flagged isError ──────────────────────────────────────────────────


def _bridge_returning(result: mcp_types.CallToolResult) -> MagicMock:
    """A fake ``McpBridgeCaller`` class whose ``call_tool_result`` answers *result*."""
    caller = MagicMock()
    caller.call_tool_result = AsyncMock(return_value=result)
    return MagicMock(return_value=caller)


_ERROR_BODY = {
    "code": "validation_error",
    "message": "This feature requires Notion AI.",
    "additional_data": {"tool_error_class": "entitlement_required"},
}


def test_call_is_error_writes_the_payload_and_exits_1(tmp_path, capsys):
    """The error body is evidence, so it is written; the exit status says it is not an answer."""
    out = tmp_path / "notion.probe-raw.json"
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=json.dumps(_ERROR_BODY, indent=2))], isError=True
    )

    with patch("mcpgen.cli._bridge.McpBridgeCaller", _bridge_returning(result)):
        rc = main(["call", "notion", "search", "--out", str(out)])

    assert rc == 1
    assert json.loads(out.read_text()) == _ERROR_BODY
    err = capsys.readouterr().err
    assert (
        '[call] tool returned isError: { "code": "validation_error", "message": "This feature requires Notion AI.",'
        in err
    )
    assert "Traceback" not in err


def test_call_is_error_excerpt_is_cut_at_300_characters(tmp_path, capsys):
    out = tmp_path / "demo.probe-raw.json"
    text = "quota\n  exceeded " * 40
    result = mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)], isError=True)

    with patch("mcpgen.cli._bridge.McpBridgeCaller", _bridge_returning(result)):
        rc = _cmd_call(_ns("demo", "search", str(out)))

    assert rc == 1
    assert out.read_text() == text + "\n"
    line = next(ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[call] tool returned isError: "))
    assert line.removeprefix("[call] tool returned isError: ") == " ".join(text.split())[:300] + "…"


def test_call_without_is_error_writes_the_payload_and_exits_0(tmp_path, capsys):
    out = tmp_path / "acme.probe-raw.json"
    result = mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=json.dumps(FAKE_DICT))])

    with patch("mcpgen.cli._bridge.McpBridgeCaller", _bridge_returning(result)):
        rc = _cmd_call(_ns("acme", "whoami", str(out)))

    assert rc == 0
    assert out.read_text() == json.dumps(FAKE_DICT, indent=2) + "\n"
    assert "isError" not in capsys.readouterr().err


def test_call_is_error_without_content_writes_nothing_and_exits_1(tmp_path, capsys):
    out = tmp_path / "demo.probe-raw.json"
    result = mcp_types.CallToolResult(content=[], isError=True)

    with patch("mcpgen.cli._bridge.McpBridgeCaller", _bridge_returning(result)):
        rc = _cmd_call(_ns("demo", "search", str(out)))

    assert rc == 1
    err = capsys.readouterr().err
    assert "[call] tool returned isError: (no text content)" in err
    assert "[call] note: body not written (ValueError)" in err
    assert not out.exists()


def test_call_is_error_excerpt_redacts_credentials_but_the_file_keeps_the_body(tmp_path, capsys):
    """stderr is redacted like every error line; --out is the raw payload, as for any call."""
    out = tmp_path / "demo.probe-raw.json"
    body = {"error": "bad grant", "access_token": "sk-live-SECRET"}
    result = mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=json.dumps(body))], isError=True)

    with patch("mcpgen.cli._bridge.McpBridgeCaller", _bridge_returning(result)):
        rc = _cmd_call(_ns("demo", "search", str(out)))

    assert rc == 1
    err = capsys.readouterr().err
    assert "SECRET" not in err
    assert '[call] tool returned isError: {"error": "bad grant", "access_token": "<redacted>"}' in err
    assert json.loads(out.read_text()) == body


@pytest.mark.parametrize(
    ("text", "expected"),
    [("{[1]: 2}", "TypeError"), ("{1, 2}", "TypeError"), ("[" * 100_000 + "]" * 100_000, "RecursionError")],
    ids=["unhashable-key", "set-not-json", "too-deep"],
)
def test_call_is_error_body_that_cannot_be_written_still_reports_and_exits_1(tmp_path, capsys, text, expected):
    """A body the parse chain or json.dumps rejects: marker, exit 1, no --out file, no traceback."""
    out = tmp_path / "demo.probe-raw.json"
    result = mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)], isError=True)

    with patch("mcpgen.cli._bridge.McpBridgeCaller", _bridge_returning(result)):
        rc = _cmd_call(_ns("demo", "search", str(out)))

    assert rc == 1
    err = capsys.readouterr().err
    assert "[call] tool returned isError: " in err
    assert f"[call] note: body not written ({expected})" in err
    assert not out.exists()
