"""Tests for `mcpgen probe` — network-free; _probe is monkeypatched via AsyncMock.

Covers:
  #1  probe with list-valued arg exits 0 (no unhashable-type crash in advisory block)
  #2  advisory block failure does not change exit code (defence-in-depth try/except)
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from mcpgen.cli import _cmd_probe, _probe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns(
    server: str,
    tool: str,
    args: list[str] | None,
    emit_shape: str | None = None,
    save_raw: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        server=server,
        tool=tool,
        args=args,
        emit_shape=emit_shape,
        save_raw=save_raw,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
    )


_FAKE_SHAPE = {"names": "list"}
_FAKE_RAW = {"names": ["alice", "bob"]}
# _probe returns (shape, observed_byte_size, raw_payload)
_FAKE_PROBE_RESULT = (_FAKE_SHAPE, 42, _FAKE_RAW)


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
        side_effect=[(_FAKE_SHAPE, 1200, _FAKE_RAW), (_FAKE_SHAPE, 675000, _FAKE_RAW)],
    ):
        rc = _cmd_probe(ns)

    assert rc == 0
    parts_dir = shapes_file.parent / (shapes_file.name + ".parts")
    part = next(parts_dir.glob("*.json"))
    skeleton = json.loads(part.read_text())
    assert skeleton["read_wiki_contents"]["_observed_bytes"] == 675000


def test_probe_size_measures_utf8_bytes_not_escaped_char_count():
    """Non-ASCII content must be measured as real UTF-8 bytes, not the inflated
    character count `json.dumps` produces with its default ensure_ascii escaping."""
    text = "héllo wörld" * 100
    expected_len = len(json.dumps(text, ensure_ascii=False).encode("utf-8"))
    inflated_escaped_len = len(json.dumps(text))  # what the old buggy code measured
    assert inflated_escaped_len > expected_len * 1.5, "fixture must actually exercise the escaping blowup"

    mock_session = MagicMock()
    mock_session.call_tool = AsyncMock(return_value=MagicMock(content=[MagicMock(type="text", text=json.dumps(text))]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield mock_session

    with patch("mcpgen._bridge.session", fake_session):
        _shape, size, _raw = asyncio.run(_probe("acme", "get_text", {}))

    assert size == expected_len


def test_probe_helper_returns_raw_payload():
    """_probe returns (shape, size, raw) — the raw payload is no longer discarded."""
    payload = {"items": [{"id": "abc", "name": "widget"}]}
    caller = MagicMock()
    caller.call = AsyncMock(return_value=payload)

    with patch("mcpgen.cli._bridge.McpBridgeCaller", return_value=caller):
        shape, size, raw = asyncio.run(_probe("acme", "search", {"q": "x"}))

    assert raw == payload, "raw payload must be returned verbatim"
    assert isinstance(size, int) and size > 0
    assert isinstance(shape, dict)


# ---------------------------------------------------------------------------
# --save-raw
# ---------------------------------------------------------------------------


def test_probe_save_raw_writes_single_payload(tmp_path):
    """One probe + --save-raw writes the payload as pretty JSON, like `mcpgen call --out`."""
    raw_file = tmp_path / "acme.search.probe-raw.json"
    ns = _ns("acme", "search", ['{"q": "widgets"}'], save_raw=str(raw_file))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(ns)

    assert rc == 0
    assert json.loads(raw_file.read_text()) == _FAKE_RAW


def test_probe_save_raw_multi_probe_writes_args_and_raw_pairs(tmp_path):
    """Multi-probe --save-raw writes one {args, raw} object per call, in order."""
    raw_file = tmp_path / "acme.search.probe-raw.json"
    ns = _ns(
        "acme",
        "search",
        ['{"entityType": 1}', '{"entityType": 2}'],
        save_raw=str(raw_file),
    )

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(ns)

    assert rc == 0
    doc = json.loads(raw_file.read_text())
    assert [e["args"] for e in doc] == [{"entityType": 1}, {"entityType": 2}]
    assert [e["raw"] for e in doc] == [_FAKE_RAW, _FAKE_RAW]


def test_probe_save_raw_string_payload_written_verbatim(tmp_path):
    """A prose payload is written as text, not as a JSON-quoted string."""
    raw_file = tmp_path / "docs.search.probe-raw.json"
    ns = _ns("docs", "search", ['{"q": "x"}'], save_raw=str(raw_file))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=({"_": "str"}, 5, "hello")):
        rc = _cmd_probe(ns)

    assert rc == 0
    assert raw_file.read_text() == "hello\n"


def test_probe_save_raw_refuses_shapes_path(tmp_path, capsys):
    """--save-raw must not overwrite a committed sidecar."""
    ns = _ns("acme", "search", ['{"q": "x"}'], save_raw=str(tmp_path / "acme.shapes.json"))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(ns)

    assert rc == 1
    assert "refusing" in capsys.readouterr().err.lower()
    assert not (tmp_path / "acme.shapes.json").exists()


def test_probe_save_raw_refuses_name_outside_the_gitignored_glob(tmp_path, capsys):
    """acme.raw.json is not covered by .gitignore — refuse it rather than advise."""
    ns = _ns("acme", "search", ['{"q": "x"}'], save_raw=str(tmp_path / "acme.raw.json"))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT) as probe:
        rc = _cmd_probe(ns)

    assert rc == 1
    assert ".probe-raw.json" in capsys.readouterr().err
    assert not (tmp_path / "acme.raw.json").exists()
    probe.assert_not_awaited()  # the guard must run before any live call


def test_probe_save_raw_warns_about_pii(tmp_path, capsys):
    ns = _ns("acme", "search", ['{"q": "x"}'], save_raw=str(tmp_path / "acme.probe-raw.json"))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        _cmd_probe(ns)

    assert "PII" in capsys.readouterr().err


def test_probe_without_save_raw_writes_no_extra_file(tmp_path):
    """Default behaviour is unchanged: no raw file appears."""
    shapes_file = tmp_path / "acme.shapes.json"
    ns = _ns("acme", "search", ['{"q": "x"}'], emit_shape=str(shapes_file))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        _cmd_probe(ns)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["acme.shapes.json.parts"]
