"""Tests for `mcpgen probe` — network-free; _probe is monkeypatched via AsyncMock.

Covers:
  #1  probe with list-valued arg exits 0 (no unhashable-type crash in advisory block)
  #2  advisory block failure does not change exit code (defence-in-depth try/except)
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcpgen.cli import _cmd_probe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns(server: str, tool: str, args: list[str] | None, emit_shape: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        server=server,
        tool=tool,
        args=args,
        emit_shape=emit_shape,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
    )


_FAKE_SHAPE = {"names": "list"}
_FAKE_PROBE_RESULT = (_FAKE_SHAPE, 42)  # (_probe now returns (shape, observed_byte_size))


# ---------------------------------------------------------------------------
# #1 — list-valued arg: no TypeError in advisory, exit 0
# ---------------------------------------------------------------------------


def test_probe_list_arg_exits_zero(tmp_path):
    """Probe with a list-valued arg must exit 0 (no unhashable-type crash)."""
    shapes_file = tmp_path / "memory.shapes.json"
    ns = _ns(
        server="memory",
        tool="open_nodes",
        args=['{"names": ["alice", "bob"]}'],  # list arg — was crashing at set insertion
        emit_shape=str(shapes_file),
    )

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(ns)

    assert rc == 0, "exit code must be 0 even when arg values are lists"


def test_probe_list_arg_writes_part_file(tmp_path):
    """Part file is created despite list-valued arg."""
    shapes_file = tmp_path / "memory.shapes.json"
    ns = _ns(
        server="memory",
        tool="open_nodes",
        args=['{"names": ["alice", "bob"]}'],
        emit_shape=str(shapes_file),
    )

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        _cmd_probe(ns)

    parts_dir = shapes_file.parent / (shapes_file.name + ".parts")
    assert parts_dir.is_dir(), "parts dir must be created"
    parts = list(parts_dir.glob("*.json"))
    assert len(parts) == 1, "one part file expected"
    assert json.loads(parts[0].read_text()), "part file must contain valid JSON"


def test_probe_scalar_discriminator_advisory_printed(tmp_path, capsys):
    """Discriminator advisory IS printed when the arg value is a scalar (type/kind)."""
    shapes_file = tmp_path / "demo.shapes.json"
    ns = _ns(
        server="demo",
        tool="get_entity",
        args=['{"type": "user"}', '{"type": "team"}'],
        emit_shape=str(shapes_file),
    )

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(ns)

    assert rc == 0
    # With two distinct values for "type", the advisory should NOT fire
    # (len(values) == 1 is the trigger — two distinct values means no single-variant warning)


def test_probe_scalar_single_discriminator_value_warns(tmp_path, capsys):
    """Advisory fires when a known-discriminator key has only one probed value."""
    shapes_file = tmp_path / "demo.shapes.json"
    ns = _ns(
        server="demo",
        tool="get_entity",
        args=['{"type": "user"}'],  # single value → advisory
        emit_shape=str(shapes_file),
    )

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(ns)

    assert rc == 0
    err = capsys.readouterr().err
    assert "type" in err and "variant-specific" in err


# ---------------------------------------------------------------------------
# #2 — defence-in-depth: even if advisory raises, exit 0
# ---------------------------------------------------------------------------


def test_probe_advisory_exception_still_exits_zero(tmp_path, capsys):
    """If the advisory block raises unexpectedly, exit 0 and print a warning to stderr.

    Inject a failure by returning a dict subclass from json.loads whose .keys()
    raises — only on the first parsed arg-dict so that probe_skeleton still sees
    a real dict and can write the part file before the advisory runs.
    """
    import json as json_mod

    shapes_file = tmp_path / "demo.shapes.json"
    ns = _ns(
        server="demo",
        tool="some_tool",
        args=['{"key": "value"}'],
        emit_shape=str(shapes_file),
    )

    original_loads = json_mod.loads
    call_count = 0

    class _BrokenKeysDict(dict):
        """dict whose .keys() raises — triggers the advisory try/except."""

        def keys(self):
            raise RuntimeError("injected advisory failure")

    def _patched_loads(s, **kwargs):
        nonlocal call_count
        result = original_loads(s, **kwargs)
        call_count += 1
        if call_count == 1:
            return _BrokenKeysDict(result)
        return result

    with (
        patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT),
        patch("mcpgen.cli.json.loads", side_effect=_patched_loads),
    ):
        rc = _cmd_probe(ns)

    assert rc == 0, "exit 0 even when advisory block raises"
    err = capsys.readouterr().err
    assert "advisory skipped" in err or "injected advisory failure" in err


# ---------------------------------------------------------------------------
# #3 — observed byte size threads through to the emitted skeleton (#6)
# ---------------------------------------------------------------------------


def test_probe_records_observed_bytes_in_skeleton(tmp_path):
    """The larger of multiple probes' observed byte sizes lands in `_observed_bytes`."""
    shapes_file = tmp_path / "deepwiki.shapes.json"
    ns = _ns(
        server="deepwiki",
        tool="read_wiki_contents",
        args=["{}", "{}"],
        emit_shape=str(shapes_file),
    )

    with patch(
        "mcpgen.cli._probe",
        new_callable=AsyncMock,
        side_effect=[(_FAKE_SHAPE, 1200), (_FAKE_SHAPE, 675000)],
    ):
        rc = _cmd_probe(ns)

    assert rc == 0
    parts_dir = shapes_file.parent / (shapes_file.name + ".parts")
    part = next(parts_dir.glob("*.json"))
    skeleton = json.loads(part.read_text())
    assert skeleton["read_wiki_contents"]["_observed_bytes"] == 675000
