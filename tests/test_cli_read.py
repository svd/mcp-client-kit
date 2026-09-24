"""Tests for `mcpgen read` — network-free; `_read_resource` or `_bridge.session` is patched."""

from __future__ import annotations

import base64
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from mcpgen import _bridge
from mcpgen.cli import _cmd_read, main

if sys.version_info < (3, 11):  # pragma: no cover - version-gated
    from exceptiongroup import BaseExceptionGroup

# ── helpers ───────────────────────────────────────────────────────────────────


def _ns(server: str, uri: str, out: str, args: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        server=server,
        uri=uri,
        args=args,
        out=out,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
    )


def _text(text: str, uri: str = "demo://config", mime: str | None = "application/json"):
    return mcp_types.TextResourceContents(uri=uri, text=text, mimeType=mime)


def _blob(data: bytes, uri: str = "demo://logo"):
    return mcp_types.BlobResourceContents(uri=uri, blob=base64.b64encode(data).decode(), mimeType="image/png")


def _result(*contents) -> mcp_types.ReadResourceResult:
    return mcp_types.ReadResourceResult(contents=list(contents))


def _patched_read(*contents):
    return patch("mcpgen.cli._read_resource", new_callable=AsyncMock, return_value=_result(*contents))


# ── output forms ──────────────────────────────────────────────────────────────


def test_read_single_text_is_written_verbatim(tmp_path):
    """No JSON re-encoding, no trailing newline added: the file is the server's text."""
    out = tmp_path / "config.probe-raw.json"
    text = '{"b": 2,  "a": 1}\r\n# not normalised'

    with _patched_read(_text(text)):
        rc = _cmd_read(_ns("demo", "demo://config", str(out)))

    assert rc == 0
    assert out.read_bytes() == text.encode("utf-8")


def test_read_single_blob_is_written_as_raw_bytes(tmp_path):
    out = tmp_path / "logo.png"
    payload = b"\x89PNG\r\n\x1a\n\x00\xff"

    with _patched_read(_blob(payload)):
        rc = _cmd_read(_ns("demo", "demo://logo", str(out)))

    assert rc == 0
    assert out.read_bytes() == payload


def test_read_multiple_contents_keep_non_ascii_text_unescaped(tmp_path):
    out = tmp_path / "many.probe-raw.json"

    with _patched_read(_text("Zürich — 東京", uri="demo://a"), _text("ok", uri="demo://b")):
        rc = _cmd_read(_ns("demo", "demo://many", str(out)))

    assert rc == 0
    assert "Zürich — 東京" in out.read_text(encoding="utf-8")


def test_read_multiple_contents_are_written_as_a_json_array_of_dumps(tmp_path):
    """Each content keeps its uri and mimeType (by alias); unset fields are dropped."""
    out = tmp_path / "many.probe-raw.json"
    blob_b64 = base64.b64encode(b"\x00\x01").decode()

    with _patched_read(_text("plain", uri="demo://a", mime=None), _blob(b"\x00\x01", uri="demo://b")):
        rc = _cmd_read(_ns("demo", "demo://many", str(out)))

    assert rc == 0
    assert json.loads(out.read_text()) == [
        {"uri": "demo://a", "text": "plain"},
        {"uri": "demo://b", "mimeType": "image/png", "blob": blob_b64},
    ]


def test_read_writes_nothing_to_stdout_and_summarises_on_stderr(tmp_path, capsys):
    out = tmp_path / "config.probe-raw.json"

    with _patched_read(_text('{"a": 1}')):
        _cmd_read(_ns("demo", "demo://config", str(out)))

    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(out) in captured.err
    assert "PII" in captured.err


# ── URI handling ──────────────────────────────────────────────────────────────


def test_read_static_uri_is_normalized_before_the_read(tmp_path):
    out = tmp_path / "x.probe-raw.json"

    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "Demo://by-unit-type/Delivery Unit", str(out)))

    assert rc == 0
    assert read.await_args.args[:2] == ("demo", "demo://by-unit-type/Delivery%20Unit")


def test_read_template_is_expanded_from_args(tmp_path):
    out = tmp_path / "x.probe-raw.json"

    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "demo://records/{record_id}{?view}", str(out), args='{"record_id": "a b"}'))

    assert rc == 0
    assert read.await_args.args[1] == "demo://records/a%20b"


def test_read_template_missing_required_variable_is_an_error(tmp_path, capsys):
    out = tmp_path / "x.probe-raw.json"

    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "demo://records/{record_id}", str(out), args='{"view": "full"}'))

    assert rc == 1
    read.assert_not_awaited()
    assert "record_id" in capsys.readouterr().err
    assert not out.exists()


def test_read_template_without_args_is_an_error_when_a_variable_is_required(tmp_path, capsys):
    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "demo://records/{record_id}", str(tmp_path / "x.probe-raw.json")))

    assert rc == 1
    read.assert_not_awaited()
    assert "record_id" in capsys.readouterr().err


def test_read_template_unknown_variable_is_an_error(tmp_path, capsys):
    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(
            _ns(
                "demo",
                "demo://records/{record_id}",
                str(tmp_path / "x.probe-raw.json"),
                args='{"record_id": "1", "recordid": "2"}',
            )
        )

    assert rc == 1
    read.assert_not_awaited()
    assert "recordid" in capsys.readouterr().err


def test_read_malformed_template_is_an_error(tmp_path, capsys):
    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "demo://records/{record_id", str(tmp_path / "x.probe-raw.json"), args="{}"))

    assert rc == 1
    read.assert_not_awaited()
    assert "[read] error" in capsys.readouterr().err


def test_read_args_on_a_static_uri_is_an_error(tmp_path, capsys):
    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "demo://config", str(tmp_path / "x.probe-raw.json"), args='{"a": 1}'))

    assert rc == 1
    read.assert_not_awaited()
    assert "--args" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["{bad json", "[1, 2]"])
def test_read_args_must_be_a_json_object(tmp_path, capsys, bad):
    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "demo://records/{record_id}", str(tmp_path / "x.probe-raw.json"), args=bad))

    assert rc == 1
    read.assert_not_awaited()
    assert "--args" in capsys.readouterr().err


def test_read_invalid_uri_is_an_error(tmp_path, capsys):
    with _patched_read(_text("{}")) as read:
        rc = _cmd_read(_ns("demo", "not a uri", str(tmp_path / "x.probe-raw.json")))

    assert rc == 1
    read.assert_not_awaited()
    assert "[read] error" in capsys.readouterr().err


# ── failures ──────────────────────────────────────────────────────────────────


def test_read_empty_contents_is_an_error(tmp_path, capsys):
    out = tmp_path / "x.probe-raw.json"

    with _patched_read():
        rc = _cmd_read(_ns("demo", "demo://config", str(out)))

    assert rc == 1
    assert "no contents" in capsys.readouterr().err
    assert not out.exists()


def test_read_invalid_base64_blob_is_an_error(tmp_path, capsys):
    out = tmp_path / "logo.png"
    bad = mcp_types.BlobResourceContents(uri="demo://logo", blob="not base64!")

    with _patched_read(bad):
        rc = _cmd_read(_ns("demo", "demo://logo", str(out)))

    assert rc == 1
    assert "[read] error" in capsys.readouterr().err
    assert not out.exists()


def test_read_mcp_error_exits_1_without_a_traceback(tmp_path, capsys):
    error = McpError(mcp_types.ErrorData(code=-32002, message="Unknown resource: demo://missing"))

    with patch("mcpgen.cli._read_resource", new_callable=AsyncMock, side_effect=error):
        rc = main(["read", "demo", "demo://missing", "--out", str(tmp_path / "x.probe-raw.json")])

    assert rc == 1
    err = capsys.readouterr().err
    assert "Unknown resource" in err
    assert "Traceback" not in err


def test_read_unknown_resource_through_the_bridge_exits_1(tmp_path, capsys):
    """The real one-shot path: the transport's nested ExceptionGroup must not reach the CLI."""
    error = McpError(mcp_types.ErrorData(code=-32002, message="Unknown resource: demo://missing"))
    s = MagicMock()
    s.read_resource = AsyncMock(side_effect=error)

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        try:
            yield s
        except BaseException as exc:
            inner = BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])
            raise BaseExceptionGroup("unhandled errors in a TaskGroup", [inner]) from None

    with patch("mcpgen._bridge.session", fake_session):
        rc = main(["read", "demo", "demo://missing", "--stdio", "python srv.py", "--out", str(tmp_path / "x.json")])

    assert rc == 1
    assert "Unknown resource" in capsys.readouterr().err


def test_read_missing_out_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info:
        main(["read", "demo", "demo://config"])
    assert exc_info.value.code == 2


def test_read_passes_connection_flags(tmp_path):
    with _patched_read(_text("{}")) as read:
        rc = main(
            [
                "read",
                "demo",
                "demo://config",
                "--stdio",
                "python srv.py",
                "--config",
                "/tmp/servers.json",
                "--out",
                str(tmp_path / "x.probe-raw.json"),
            ]
        )

    assert rc == 0
    kwargs = read.await_args.kwargs
    assert kwargs["cmd"] == "python srv.py"
    assert kwargs["config_path"] == "/tmp/servers.json"


def test_read_write_failure_exits_1_without_a_traceback(tmp_path, capsys):
    """--out naming a directory is an OSError from the write, reported like any other failure."""
    with _patched_read(_text("{}")):
        rc = main(["read", "demo", "demo://config", "--out", str(tmp_path)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "[read] error" in err
    assert "Traceback" not in err


def _grouped(exc: BaseException) -> BaseExceptionGroup:
    inner = BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])


def _failing_open_session(exc: BaseException):
    """A fake ``session`` CM that fails while opening, as a crashed stdio server or failed initialize does."""

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        raise _grouped(exc)
        yield  # pragma: no cover - makes this an async generator

    return fake_session


def test_read_grouped_mcp_error_on_session_open_exits_1(tmp_path, capsys):
    error = McpError(mcp_types.ErrorData(code=-32000, message="Connection closed"))

    with patch("mcpgen._bridge.session", _failing_open_session(error)):
        rc = main(["read", "demo", "demo://config", "--stdio", "python srv.py", "--out", str(tmp_path / "x.json")])

    assert rc == 1
    err = capsys.readouterr().err
    assert "[read] error: McpError: Connection closed" in err
    assert "Traceback" not in err


def test_read_grouped_auth_error_reaches_the_auth_handler(tmp_path, capsys):
    """A mid-request 401 raises ReauthenticationRequired in a transport task; main() reports it."""
    auth = _bridge.ReauthenticationRequired("OAuth re-auth needed for 'demo'. Run: mcpgen login demo")

    with patch("mcpgen._bridge.session", _failing_open_session(auth)):
        rc = main(["read", "demo", "demo://config", "--out", str(tmp_path / "x.json")])

    assert rc == 1
    assert "[mcpgen] error: OAuth re-auth needed for 'demo'. Run: mcpgen login demo" in capsys.readouterr().err


def test_read_grouped_bug_still_propagates(tmp_path):
    """Only McpError/ValueError groups are reported; anything else is a bug and must traceback."""
    with patch("mcpgen._bridge.session", _failing_open_session(KeyError("boom"))):
        with pytest.raises(BaseExceptionGroup) as excinfo:
            main(["read", "demo", "demo://config", "--out", str(tmp_path / "x.json")])

    assert excinfo.group_contains(KeyError)
