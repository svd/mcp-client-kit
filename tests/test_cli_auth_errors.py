"""Tests for the top-level auth-error handler in `cli.main` — network-free.

Only `mcpgen login` caught the auth taxonomy. Every other command that reaches a server
— `list`, `codegen`, `probe`, `call` — let `ReauthenticationRequired` and
`LoginWontHelp` reach the interpreter, which prints a traceback for what is a routine
operational condition. That is bad CLI behaviour on its own, and it is the path that
carried the token response into CI logs before the raise sites stopped chaining the
pydantic error.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcpgen import _bridge, cli


@pytest.mark.parametrize("command", ["list", "codegen", "probe", "call"])
@pytest.mark.parametrize(
    "exc",
    [
        _bridge.ReauthenticationRequired("credential is dead. Run: mcpgen login acme"),
        _bridge.TokenRefreshUnavailable("endpoint down; retry later."),
        _bridge.PostLoginCheckFailed("token cached but the check after it failed"),
        _bridge.LoginWontHelp("store could not be moved aside"),
    ],
    ids=["reauth", "refresh-unavailable", "post-login", "wont-help"],
)
def test_main_reports_auth_failures_as_a_message(capsys, tmp_path, command, exc):
    """The message, exit 1, and no traceback — on every command, not just `login`.

    Catching the two roots covers both subclasses and any command added later, which is
    the failure mode of widening each `_cmd_*` except tuple by hand.
    """
    argv = {
        "list": ["list", "acme"],
        "codegen": ["codegen", "acme"],
        "probe": ["probe", "acme", "some_tool"],
        "call": ["call", "acme", "some_tool", "--out", str(tmp_path / "raw.json")],
    }[command]

    with (
        patch("mcpgen.cli._list_tools", side_effect=exc),
        patch("mcpgen.cli._probe", side_effect=exc),
        patch("mcpgen.cli._call", side_effect=exc),
    ):
        assert cli.main(argv) == 1

    err = capsys.readouterr().err
    assert str(exc) in err
    assert "Traceback" not in err


def test_main_still_tracebacks_on_a_real_bug(tmp_path):
    """The catch stays narrow on purpose.

    A KeyError or AttributeError from a genuine defect must reach the interpreter so it
    is reported as a bug rather than printed as if it were an operational condition.
    """
    with patch("mcpgen.cli._list_tools", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            cli.main(["list", "acme"])


def test_check_keeps_its_own_exit_code_for_an_auth_failure(tmp_path):
    """`check` is deliberately outside this handler, and that has to stay true.

    Its exit codes are a documented contract: 1 means the tool inventory drifted, 2 means
    it could not be determined — a missing manifest, or a transport/auth/config failure.
    `_cmd_check` catches broadly and returns 2 before `main()` sees anything, so an expired
    credential must not come back as 1 and be read as drift.
    """
    manifest = tmp_path / "acme.mcpgen.json"
    manifest.write_text('{"tools": []}')
    exc = _bridge.ReauthenticationRequired("credential is dead")

    with patch("mcpgen.cli._list_tools", side_effect=exc):
        assert cli.main(["check", "acme", "--manifest", str(manifest)]) == 2
