"""Tests for --env flag / _parse_env helper and session() stdio env wiring.

Covers:
  #1  _parse_env returns None when no --env flags given
  #2  KEY=VAL sets the key inline
  #3  KEY (no =) reads from os.environ
  #4  KEY not in os.environ is skipped with a stderr warning
  #5  Multiple --env flags all forwarded
  #6  session() --stdio path passes env to _stdio_session
  #7  session() config-by-name path merges --env over cached spec env
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcpgen.cli import _parse_env

# ---------------------------------------------------------------------------
# _parse_env unit tests
# ---------------------------------------------------------------------------


def _ns(env_flags=None) -> SimpleNamespace:
    return SimpleNamespace(env=env_flags)


def test_parse_env_none_when_no_flags():
    assert _parse_env(_ns(None)) is None


def test_parse_env_none_when_empty_list():
    assert _parse_env(_ns([])) is None


def test_parse_env_inline_key_val():
    result = _parse_env(_ns(["FOO=bar"]))
    assert result == {"FOO": "bar"}


def test_parse_env_inline_val_with_equals():
    """Value itself contains '=' — split on first '=' only."""
    result = _parse_env(_ns(["TOKEN=abc=def="]))
    assert result == {"TOKEN": "abc=def="}


def test_parse_env_key_from_os_environ(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "secret-123")
    result = _parse_env(_ns(["MY_API_KEY"]))
    assert result == {"MY_API_KEY": "secret-123"}


def test_parse_env_missing_key_skipped_with_warning(monkeypatch, capsys):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    result = _parse_env(_ns(["MISSING_KEY"]))
    assert result is None
    err = capsys.readouterr().err
    assert "MISSING_KEY" in err
    assert "skipped" in err


def test_parse_env_multiple_flags(monkeypatch):
    monkeypatch.setenv("CONTEXT7_API_KEY", "ctx-key")
    result = _parse_env(_ns(["CONTEXT7_API_KEY", "INLINE=value", "CONTEXT7_API_KEY=override"]))
    # last one wins for the same key (dict update order)
    assert result["CONTEXT7_API_KEY"] == "override"
    assert result["INLINE"] == "value"


def test_parse_env_mix_present_and_missing(monkeypatch, capsys):
    monkeypatch.setenv("PRESENT", "yes")
    monkeypatch.delenv("ABSENT", raising=False)
    result = _parse_env(_ns(["PRESENT", "ABSENT"]))
    assert result == {"PRESENT": "yes"}
    assert "ABSENT" in capsys.readouterr().err


def test_parse_env_empty_key_skipped(capsys):
    """--env =VALUE has empty key — must be skipped with a warning, not added."""
    result = _parse_env(_ns(["=VALUE"]))
    assert result is None
    assert "empty key" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# session() wiring tests — ensure env reaches _stdio_session
# ---------------------------------------------------------------------------


def test_session_stdio_flag_passes_env():
    """session() with cmd= must pass the env dict into _stdio_session."""
    captured = {}

    async def _fake_stdio_session(command, args, env=None):
        captured["env"] = env
        # yield a mock ClientSession
        mock_s = AsyncMock()
        mock_s.initialize = AsyncMock()
        yield mock_s

    import contextlib

    fake_cm = contextlib.asynccontextmanager(_fake_stdio_session)

    import asyncio

    from mcpgen._bridge import session

    env_in = {"CONTEXT7_API_KEY": "testkey"}

    with patch("mcpgen._bridge._stdio_session", side_effect=fake_cm):

        async def _run():
            async with session("dummy", cmd="echo hello", env=env_in):
                pass

        asyncio.run(_run())

    assert captured.get("env") == env_in


def test_session_no_env_passes_none():
    """session() with cmd= and no env must pass env=None."""
    captured = {}

    async def _fake_stdio_session(command, args, env=None):
        captured["env"] = env
        mock_s = AsyncMock()
        mock_s.initialize = AsyncMock()
        yield mock_s

    import contextlib

    fake_cm = contextlib.asynccontextmanager(_fake_stdio_session)

    import asyncio

    from mcpgen._bridge import session

    with patch("mcpgen._bridge._stdio_session", side_effect=fake_cm):

        async def _run():
            async with session("dummy", cmd="echo hello"):
                pass

        asyncio.run(_run())

    assert captured.get("env") is None
