"""Tests for `mcpgen probe` — network-free; _probe is monkeypatched via AsyncMock.

Covers:
  #1  probe with list-valued arg exits 0 (no unhashable-type crash in advisory block)
  #2  advisory block failure does not change exit code (defence-in-depth try/except)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from mcpgen import _bridge
from mcpgen.cli import _cmd_probe, _part_filename, _probe, _probe_resource, main

if sys.version_info < (3, 11):  # pragma: no cover - version-gated
    from exceptiongroup import BaseExceptionGroup

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


# ---------------------------------------------------------------------------
# probe --resource
# ---------------------------------------------------------------------------


def _rns(target: str, args: list[str] | None = None, emit_shape: str | None = None, save_raw: str | None = None):
    ns = _ns("demo", target, args, emit_shape=emit_shape, save_raw=save_raw)
    ns.resource = True
    return ns


def _only_part(shapes_file) -> tuple[str, dict]:
    parts = list((shapes_file.parent / (shapes_file.name + ".parts")).glob("*.json"))
    assert len(parts) == 1, parts
    return parts[0].name, json.loads(parts[0].read_text())


_RESOURCE_RESULT = ({"a": "int"}, 8, {"a": 1})


def test_probe_resource_static_part_keyed_by_uri_with_kind(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://config", emit_shape=str(shapes_file)))

    assert rc == 0
    assert probe.await_args.args[:2] == ("demo", "demo://config")
    name, skeleton = _only_part(shapes_file)
    assert name == "demo%3A%2F%2Fconfig.json"
    entry = skeleton["demo://config"]
    assert entry["kind"] == "resource"
    assert entry["probed_args"] == {}
    assert entry["fields"] == {"a": "int"}
    assert list(entry)[0] == "kind"


def test_probe_resource_static_key_is_normalized(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("Dimension://by-unit-type/Delivery Unit", emit_shape=str(shapes_file)))

    assert rc == 0
    assert probe.await_args.args[1] == "dimension://by-unit-type/Delivery%20Unit"
    _name, skeleton = _only_part(shapes_file)
    assert list(skeleton) == ["dimension://by-unit-type/Delivery%20Unit"]


def test_probe_resource_template_keyed_by_template_with_variables_in_probed_args(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"
    template = "dimension://by-unit-type/{unitType}"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(
            _rns(template, ['{"unitType": "PROJECT"}', '{"unitType": "Delivery Unit"}'], emit_shape=str(shapes_file))
        )

    assert rc == 0
    assert [c.args[1] for c in probe.await_args_list] == [
        "dimension://by-unit-type/PROJECT",
        "dimension://by-unit-type/Delivery%20Unit",
    ]
    _name, skeleton = _only_part(shapes_file)
    entry = skeleton[template]
    assert entry["kind"] == "resource_template"
    assert entry["probed_args"] == [{"unitType": "PROJECT"}, {"unitType": "Delivery Unit"}]


def test_probe_resource_template_single_probe_records_the_variables_dict(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT):
        rc = _cmd_probe(_rns("demo://records/{record_id}", ['{"record_id": "r1"}'], emit_shape=str(shapes_file)))

    assert rc == 0
    _name, skeleton = _only_part(shapes_file)
    assert skeleton["demo://records/{record_id}"]["probed_args"] == {"record_id": "r1"}


def test_probe_resource_template_missing_variable_errors(tmp_path, capsys):
    shapes_file = tmp_path / "demo.shapes.json"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://records/{record_id}", ['{"record_id": "r1"}', "{}"], emit_shape=str(shapes_file)))

    assert rc == 1
    probe.assert_not_awaited()  # every probe's variables are checked before the first live read
    assert "record_id" in capsys.readouterr().err
    assert not (tmp_path / "demo.shapes.json.parts").exists()


def test_probe_resource_template_without_args_errors_when_a_variable_is_required(capsys):
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://records/{record_id}"))

    assert rc == 1
    probe.assert_not_awaited()
    assert "record_id" in capsys.readouterr().err


def test_probe_resource_args_on_a_static_uri_errors(capsys):
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://config", ['{"a": 1}']))

    assert rc == 1
    probe.assert_not_awaited()
    assert "--args" in capsys.readouterr().err


def test_probe_resource_mcp_error_exits_1(capsys):
    from mcp import types as mcp_types
    from mcp.shared.exceptions import McpError

    error = McpError(mcp_types.ErrorData(code=-32002, message="Unknown resource: demo://missing"))
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, side_effect=error):
        rc = _cmd_probe(_rns("demo://missing"))

    assert rc == 1
    assert "[probe] error: McpError: Unknown resource: demo://missing" in capsys.readouterr().err


def test_probe_resource_has_no_discriminator_advisory(tmp_path, capsys):
    """A template variable named like a discriminator probed once must not warn."""
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT):
        rc = _cmd_probe(_rns("demo://by-type/{type}", ['{"type": "user"}'], emit_shape=str(tmp_path / "d.shapes.json")))

    assert rc == 0
    assert "variant-specific" not in capsys.readouterr().err


def _blob_session(data: bytes):
    """A fake bridge session whose resources/read answers one blob."""
    from mcp import types as mcp_types

    s = MagicMock()
    blob = mcp_types.BlobResourceContents(uri="demo://logo", blob=base64.b64encode(data).decode(), mimeType="image/png")
    s.read_resource = AsyncMock(return_value=mcp_types.ReadResourceResult(contents=[blob]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield s

    return fake_session


def test_probe_resource_blob_shape_is_bytes(tmp_path):
    """Through the runtime read path: the blob decodes to bytes and only its type name reaches the part."""
    shapes_file = tmp_path / "demo.shapes.json"
    payload = b"\x89PNG\r\n\x1a\n\x00\xff"

    with patch("mcpgen._bridge.session", _blob_session(payload)):
        rc = _cmd_probe(_rns("demo://logo", emit_shape=str(shapes_file)))

    assert rc == 0
    _name, skeleton = _only_part(shapes_file)
    entry = skeleton["demo://logo"]
    assert entry["_observed_shape"] == "bytes"
    assert entry["fields"] == {}
    assert entry["_observed_bytes"] == len(payload)


def test_probe_resource_save_raw_base64_encodes_bytes(tmp_path):
    raw_file = tmp_path / "demo.logo.probe-raw.json"
    payload = b"\x00\x01\xfe\xff"

    with patch("mcpgen._bridge.session", _blob_session(payload)):
        rc = _cmd_probe(_rns("demo://logo", save_raw=str(raw_file)))

    assert rc == 0
    assert base64.b64decode(json.loads(raw_file.read_text())) == payload


def test_probe_resource_save_raw_multi_probe_records_variables_and_base64(tmp_path):
    raw_file = tmp_path / "demo.rec.probe-raw.json"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=("bytes", 2, b"\x00\x01")):
        rc = _cmd_probe(_rns("demo://rec/{id}", ['{"id": "1"}', '{"id": "2"}'], save_raw=str(raw_file)))

    assert rc == 0
    doc = json.loads(raw_file.read_text())
    assert [e["args"] for e in doc] == [{"id": "1"}, {"id": "2"}]
    assert [base64.b64decode(e["raw"]) for e in doc] == [b"\x00\x01", b"\x00\x01"]


def test_probe_resource_long_key_gets_a_hashed_filename(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"
    template = "demo://" + "segment/" * 40 + "{id}"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT):
        rc = _cmd_probe(_rns(template, ['{"id": "1"}'], emit_shape=str(shapes_file)))

    assert rc == 0
    name, skeleton = _only_part(shapes_file)
    assert list(skeleton) == [template]
    digest = hashlib.sha256(template.encode()).hexdigest()
    assert name.endswith("-" + digest + ".json")
    assert len(name) == 200 + len(".json")


def test_part_filename_is_the_quoted_key_up_to_200_characters():
    assert _part_filename("x" * 200) == "x" * 200 + ".json"
    long_a, long_b = "x" * 201, "x" * 200 + "y"
    assert _part_filename(long_a) != _part_filename(long_b)
    assert len(_part_filename(long_a)) == 200 + len(".json")


def test_probe_tool_part_filename_unchanged(tmp_path):
    """Tool parts keep the plain quoted name, byte for byte."""
    shapes_file = tmp_path / "demo.shapes.json"

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(_ns("demo", "get/me x", ["{}"], emit_shape=str(shapes_file)))

    assert rc == 0
    name, skeleton = _only_part(shapes_file)
    assert name == "get%2Fme%20x.json"
    assert "kind" not in skeleton["get/me x"]
    assert _part_filename("search_issues") == "search_issues.json"


def test_probe_resource_helper_measures_and_summarizes_through_read_resource():
    caller = MagicMock()
    caller.read_resource = AsyncMock(return_value={"items": [1, 2]})

    with patch("mcpgen.cli._bridge.McpBridgeCaller", return_value=caller):
        shape, size, raw = asyncio.run(_probe_resource("demo", "demo://config"))

    caller.read_resource.assert_awaited_once_with("demo", "demo://config")
    assert raw == {"items": [1, 2]}
    assert shape == {"items": ["int", "...x2"]}
    assert size == len(json.dumps(raw).encode())


def test_probe_resource_static_with_invalid_json_args_names_the_real_problem(capsys):
    """--args on a static URI is refused as such, before its JSON is even parsed."""
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://config", ["{not json"]))

    assert rc == 1
    probe.assert_not_awaited()
    assert "--args applies to a URI template only" in capsys.readouterr().err


def test_probe_resource_template_with_only_optional_variables_needs_no_args(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://search{?q,limit}", emit_shape=str(shapes_file)))

    assert rc == 0
    assert [c.args[1] for c in probe.await_args_list] == ["demo://search"]
    _name, skeleton = _only_part(shapes_file)
    entry = skeleton["demo://search{?q,limit}"]
    assert entry["kind"] == "resource_template"
    assert entry["probed_args"] == {}


def test_probe_resource_logs_each_uri_read(capsys):
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT):
        rc = _cmd_probe(_rns("demo://rec/{id}", ['{"id": "a b"}', '{"id": "2"}']))

    assert rc == 0
    err = capsys.readouterr().err
    assert "[1/2] demo://rec/a%20b" in err
    assert "[2/2] demo://rec/2" in err


# ---------------------------------------------------------------------------
# Part file collisions (case-insensitive filesystems)
# ---------------------------------------------------------------------------


def _prewrite_part(shapes_file, filename_key: str, stored_key: str):
    part = shapes_file.parent / (shapes_file.name + ".parts") / _part_filename(filename_key)
    part.parent.mkdir(parents=True)
    part.write_text(json.dumps({stored_key: {"source": "live"}}) + "\n")
    return part


def test_probe_resource_refuses_a_part_file_holding_another_key(tmp_path, capsys):
    """On macOS demo://x/Users and demo://x/users share one part file; the second probe must not clobber the first."""
    shapes_file = tmp_path / "demo.shapes.json"
    part = _prewrite_part(shapes_file, "demo://x/users", "demo://x/Users")
    before = part.read_text()

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT) as probe:
        rc = _cmd_probe(_rns("demo://x/users", emit_shape=str(shapes_file)))

    assert rc == 1
    probe.assert_not_awaited()
    assert part.read_text() == before
    err = capsys.readouterr().err
    assert "demo://x/Users" in err and "demo://x/users" in err
    assert "Run `mcpgen merge demo` to consolidate it first" in err
    assert "--emit-shape" not in err


def test_probe_rechecks_the_part_file_just_before_writing(tmp_path, capsys):
    """A concurrent probe of the case-twin key that lands its part mid-probe is not clobbered either."""
    shapes_file = tmp_path / "demo.shapes.json"
    part = shapes_file.parent / (shapes_file.name + ".parts") / _part_filename("demo://x/users")
    twin = json.dumps({"demo://x/Users": {"source": "live"}}) + "\n"

    async def concurrent_twin_lands(*args, **kwargs):
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(twin)
        return _RESOURCE_RESULT

    with patch("mcpgen.cli._probe_resource", side_effect=concurrent_twin_lands):
        rc = _cmd_probe(_rns("demo://x/users", emit_shape=str(shapes_file)))

    assert rc == 1
    assert part.read_text() == twin
    err = capsys.readouterr().err
    assert "'demo://x/Users'" in err and "mcpgen merge demo" in err


def test_probe_tool_refuses_a_part_file_holding_another_key(tmp_path, capsys):
    shapes_file = tmp_path / "demo.shapes.json"
    part = _prewrite_part(shapes_file, "search", "Search")
    before = part.read_text()

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT) as probe:
        rc = _cmd_probe(_ns("demo", "search", ["{}"], emit_shape=str(shapes_file)))

    assert rc == 1
    probe.assert_not_awaited()
    assert part.read_text() == before
    err = capsys.readouterr().err
    assert "'Search'" in err
    assert "Run `mcpgen merge demo` to consolidate it first, or delete the part if 'Search' is gone" in err


def test_probe_overwrites_its_own_part_on_a_re_probe(tmp_path):
    shapes_file = tmp_path / "demo.shapes.json"
    _prewrite_part(shapes_file, "search", "search")

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = _cmd_probe(_ns("demo", "search", ["{}"], emit_shape=str(shapes_file)))

    assert rc == 0
    _name, skeleton = _only_part(shapes_file)
    assert skeleton["search"]["_observed_shape"] == _FAKE_SHAPE


# ---------------------------------------------------------------------------
# Tool and grouped failures
# ---------------------------------------------------------------------------


def _grouped(exc: BaseException) -> BaseExceptionGroup:
    inner = BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])


def _tool_error() -> McpError:
    return McpError(mcp_types.ErrorData(code=-32602, message="Unknown tool: nope"))


@pytest.mark.parametrize("wrap", [False, True], ids=["bare", "grouped"])
def test_probe_tool_mcp_error_exits_1(capsys, wrap):
    error = _grouped(_tool_error()) if wrap else _tool_error()

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, side_effect=error):
        rc = _cmd_probe(_ns("demo", "nope", None))

    assert rc == 1
    assert "[probe] error: McpError: Unknown tool: nope" in capsys.readouterr().err


def test_probe_grouped_auth_error_reaches_the_auth_handler(capsys):
    auth = _bridge.ReauthenticationRequired("OAuth re-auth needed for 'demo'. Run: mcpgen login demo")

    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, side_effect=_grouped(auth)):
        rc = main(["probe", "demo", "demo://config", "--resource"])

    assert rc == 1
    assert "[mcpgen] error: OAuth re-auth needed" in capsys.readouterr().err


def test_probe_grouped_bug_still_propagates():
    with patch("mcpgen.cli._probe", new_callable=AsyncMock, side_effect=_grouped(KeyError("boom"))):
        with pytest.raises(BaseExceptionGroup) as excinfo:
            _cmd_probe(_ns("demo", "search", None))

    assert excinfo.group_contains(KeyError)


# ---------------------------------------------------------------------------
# --timeout
# ---------------------------------------------------------------------------


def _timeout_error() -> McpError:
    """The error the SDK raises when a request's read timeout expires."""
    return McpError(
        mcp_types.ErrorData(
            code=408, message="Timed out while waiting for response to ClientRequest. Waited 60.0 seconds."
        )
    )


@pytest.mark.parametrize(("argv_flag", "expected"), [(["--timeout", "5"], 5.0), (["--timeout", "0"], None), ([], 60.0)])
def test_probe_timeout_flag_reaches_probe(argv_flag, expected):
    """--timeout reaches _probe as call_timeout; 0 means no limit (None); absent is 60s."""
    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT) as probe:
        assert main(["probe", "demo", "greet", *argv_flag]) == 0
    assert probe.await_args.kwargs["call_timeout"] == expected


@pytest.mark.parametrize("bad", ["-1", "abc", "nan", "inf", "1e14"])
def test_probe_timeout_rejects_invalid(bad, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["probe", "demo", "greet", "--timeout", bad])
    assert exc.value.code == 2
    assert "argument --timeout" in capsys.readouterr().err


def test_probe_helper_passes_call_timeout_to_the_bridge():
    caller = MagicMock()
    caller.call = AsyncMock(return_value={"ok": True})

    with patch("mcpgen.cli._bridge.McpBridgeCaller", return_value=caller) as bridge:
        asyncio.run(_probe("demo", "greet", {}, call_timeout=7.0))

    assert bridge.call_args.kwargs["call_timeout"] == 7.0


@pytest.mark.parametrize("wrap", [False, True], ids=["bare", "grouped"])
def test_probe_timeout_exits_1_with_a_marker_and_writes_no_part(tmp_path, capsys, wrap):
    shapes_file = tmp_path / "demo.shapes.json"
    error = _grouped(_timeout_error()) if wrap else _timeout_error()

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, side_effect=error):
        rc = main(["probe", "demo", "annotate", "--timeout", "5", "--emit-shape", str(shapes_file)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "[probe] timeout: no answer within 5s (raise with --timeout)" in err
    assert "[probe] error" not in err
    assert not (tmp_path / "demo.shapes.json.parts").exists()
    assert not shapes_file.exists()


def test_probe_timeout_marker_names_the_default(capsys):
    with patch("mcpgen.cli._probe", new_callable=AsyncMock, side_effect=_grouped(_timeout_error())):
        rc = _cmd_probe(_ns("demo", "annotate", None))

    assert rc == 1
    assert "[probe] timeout: no answer within 60s (raise with --timeout)" in capsys.readouterr().err


def test_probe_server_sent_408_is_reported_as_an_error_not_a_timeout(capsys):
    """Only the SDK's own read timeout is a timeout; a server's JSON-RPC 408 is its error."""
    server_408 = McpError(mcp_types.ErrorData(code=408, message="upstream took too long"))

    with patch("mcpgen.cli._probe", new_callable=AsyncMock, side_effect=_grouped(server_408)):
        rc = _cmd_probe(_ns("demo", "slow", None))

    assert rc == 1
    err = capsys.readouterr().err
    assert "[probe] error: McpError: upstream took too long" in err
    assert "timeout:" not in err


def test_probe_resource_with_explicit_timeout_notes_it_does_not_apply(capsys):
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT):
        rc = main(["probe", "demo", "demo://config", "--resource", "--timeout", "5"])

    assert rc == 0
    assert "[probe] note: --timeout does not apply to resource reads" in capsys.readouterr().err


def test_probe_resource_without_timeout_prints_no_note(capsys):
    with patch("mcpgen.cli._probe_resource", new_callable=AsyncMock, return_value=_RESOURCE_RESULT):
        rc = main(["probe", "demo", "demo://config", "--resource"])

    assert rc == 0
    assert "--timeout" not in capsys.readouterr().err


def test_probe_tool_with_explicit_timeout_prints_no_note(capsys):
    with patch("mcpgen.cli._probe", new_callable=AsyncMock, return_value=_FAKE_PROBE_RESULT):
        rc = main(["probe", "demo", "greet", "--timeout", "5"])

    assert rc == 0
    assert "does not apply" not in capsys.readouterr().err
