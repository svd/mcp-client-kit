"""Tests for per-tool shapes-part writing, atomic helper, merge_skeletons, and
the `mcp-kit merge` subcommand.

These tests are network-free: _probe is stubbed wherever the probe path is
exercised.  The concurrency test verifies that parallel writes of *distinct*
tool parts produce no clobbered files.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mcp_client_kit import codegen
from mcp_client_kit.cli import (
    _atomic_write_text,
    _cmd_merge,
    _parts_dir,
)


# ── merge_skeletons ───────────────────────────────────────────────────────────

def test_merge_skeletons_empty():
    assert codegen.merge_skeletons([]) == {}


def test_merge_skeletons_single():
    sk = {"get_entity": {"unwrap": [], "fields": {"id": "str"}}}
    assert codegen.merge_skeletons([sk]) == sk


def test_merge_skeletons_union():
    a = {"get_entity": {"fields": {"id": "str"}}}
    b = {"query_acme": {"fields": {"results": "list"}}}
    merged = codegen.merge_skeletons([a, b])
    assert set(merged.keys()) == {"get_entity", "query_acme"}


def test_merge_skeletons_later_wins():
    base = {"get_entity": {"fields": {"id": "str"}, "source": "fixture"}}
    part = {"get_entity": {"fields": {"id": "str", "name": "str"}, "source": "live"}}
    merged = codegen.merge_skeletons([base, part])
    assert merged["get_entity"]["source"] == "live"
    assert "name" in merged["get_entity"]["fields"]


def test_merge_skeletons_preserves_unprobed_base():
    base = {"hand_edited": {"unwrap": ["data"], "return_model": "Entity"}}
    part = {"new_tool": {"fields": {"x": "int"}}}
    merged = codegen.merge_skeletons([base, part])
    # Base entry NOT in parts is preserved as-is.
    assert merged["hand_edited"] == base["hand_edited"]
    assert "new_tool" in merged


# ── _atomic_write_text ────────────────────────────────────────────────────────

def test_atomic_write_text_creates_file(tmp_path):
    target = tmp_path / "out.json"
    _atomic_write_text(target, '{"x": 1}')
    assert target.read_text() == '{"x": 1}'


def test_atomic_write_text_creates_parent(tmp_path):
    target = tmp_path / "subdir" / "out.json"
    _atomic_write_text(target, "hello")
    assert target.exists()


def test_atomic_write_text_no_tmp_left_on_success(tmp_path):
    target = tmp_path / "out.json"
    _atomic_write_text(target, "ok")
    tmp_files = list(tmp_path.glob("*.tmp.*"))
    assert tmp_files == [], f"stale tmp: {tmp_files}"


def test_atomic_write_text_overwrites(tmp_path):
    target = tmp_path / "out.json"
    _atomic_write_text(target, "first")
    _atomic_write_text(target, "second")
    assert target.read_text() == "second"


# ── _parts_dir ────────────────────────────────────────────────────────────────

def test_parts_dir_name():
    p = _parts_dir(Path("/work/acme.shapes.json"))
    assert p == Path("/work/acme.shapes.json.parts")


# ── probe emit path (unit) ────────────────────────────────────────────────────

def test_probe_writes_part_not_shared_file(tmp_path, monkeypatch):
    """Probe with --emit-shape writes a per-tool part, NOT the shared target."""
    from mcp_client_kit.cli import main

    target = tmp_path / "acme.shapes.json"
    ns_tool = "get_entity"

    # Stub _probe so no network call is made.
    fake_shape = {"id": "str", "name": "str"}
    import asyncio
    monkeypatch.setattr(
        "mcp_client_kit.cli._probe",
        lambda *a, **kw: asyncio.coroutine(lambda: fake_shape)(),
    )
    # Also patch asyncio.run to call the coroutine synchronously.
    monkeypatch.setattr("asyncio.run", lambda coro: fake_shape)

    from mcp_client_kit.cli import _cmd_probe
    ns = SimpleNamespace(
        server="acme",
        tool=ns_tool,
        args=['{"entityId": "x", "entityType": 1}'],
        emit_shape=str(target),
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
    )
    with patch("mcp_client_kit.cli._probe", return_value=fake_shape), \
         patch("asyncio.run", return_value=fake_shape):
        _cmd_probe(ns)

    # Shared target must NOT exist (part was written instead).
    assert not target.exists(), "probe must not write the shared shapes.json"

    # Part file must exist under <target>.parts/.
    from urllib.parse import quote as q
    part = _parts_dir(target) / (q(ns_tool, safe="") + ".json")
    assert part.exists(), f"expected part at {part}"
    data = json.loads(part.read_text())
    assert ns_tool in data


def test_probe_url_quotes_tool_name(tmp_path):
    """Tool names with / or : are percent-encoded, not interpreted as paths."""
    from urllib.parse import quote as q
    from mcp_client_kit.cli import _parts_dir, _atomic_write_text

    target = tmp_path / "srv.shapes.json"
    tool = "ns/get:entity"  # hypothetical, has /

    parts_d = _parts_dir(target)
    part = parts_d / (q(tool, safe="") + ".json")
    _atomic_write_text(part, '{"ns%2Fget%3Aentity": {}}')

    assert part.exists()
    # Must be a flat file, not nested under 'ns/'
    assert part.parent == parts_d


# ── concurrency (no network) ──────────────────────────────────────────────────

def test_concurrent_distinct_tools_no_clobber(tmp_path):
    """N threads writing distinct tool parts concurrently produce N intact files."""
    target = tmp_path / "acme.shapes.json"
    tools = [f"tool_{i}" for i in range(16)]
    errors: list[Exception] = []

    def write_part(tool: str) -> None:
        try:
            from urllib.parse import quote as q
            part = _parts_dir(target) / (q(tool, safe="") + ".json")
            sk = {tool: {"fields": {"i": "int"}, "source": "live"}}
            _atomic_write_text(part, json.dumps(sk))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_part, args=(t,)) for t in tools]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == [], errors

    parts_d = _parts_dir(target)
    parts = list(parts_d.glob("*.json"))
    assert len(parts) == len(tools), f"got {len(parts)}, expected {len(tools)}"

    merged = codegen.merge_skeletons([json.loads(p.read_text()) for p in parts])
    assert set(merged.keys()) == set(tools), "some tool keys were lost"


# ── _cmd_merge ────────────────────────────────────────────────────────────────

def _seed_parts(target: Path, tool_skeletons: dict[str, dict]) -> None:
    """Write each skeleton as a separate part file."""
    from urllib.parse import quote as q
    parts_d = _parts_dir(target)
    for tool, sk in tool_skeletons.items():
        part = parts_d / (q(tool, safe="") + ".json")
        _atomic_write_text(part, json.dumps({tool: sk}))


def _merge_ns(server: str, target: Path, keep_parts: bool = False) -> SimpleNamespace:
    return SimpleNamespace(server=server, out=str(target), keep_parts=keep_parts)


def test_merge_union_of_parts_and_base(tmp_path):
    target = tmp_path / "acme.shapes.json"

    # Existing base with hand-edited entry.
    base = {"hand_edited": {"unwrap": ["data"], "return_model": "Entity", "source": "live"}}
    target.write_text(json.dumps(base))

    # Two parts: one overlapping base, one new.
    _seed_parts(target, {
        "hand_edited": {"unwrap": [], "return_model": None, "source": "live"},  # overlaps
        "new_tool": {"fields": {"x": "int"}, "source": "live"},
    })

    rc = _cmd_merge(_merge_ns("acme", target))

    assert rc == 0
    result = json.loads(target.read_text())
    # Part overwrites base for hand_edited (later wins).
    assert result["hand_edited"]["return_model"] is None
    assert "new_tool" in result


def test_merge_removes_parts_dir_by_default(tmp_path):
    target = tmp_path / "acme.shapes.json"
    _seed_parts(target, {"t1": {"source": "live"}})

    _cmd_merge(_merge_ns("acme", target))

    assert not _parts_dir(target).exists(), "parts dir should be cleaned up"


def test_merge_keep_parts_flag(tmp_path):
    target = tmp_path / "acme.shapes.json"
    _seed_parts(target, {"t1": {"source": "live"}})

    _cmd_merge(_merge_ns("acme", target, keep_parts=True))

    assert _parts_dir(target).exists(), "--keep-parts should retain the directory"


def test_merge_no_parts_dir_is_noop(tmp_path):
    target = tmp_path / "acme.shapes.json"
    target.write_text('{"existing": {}}')

    rc = _cmd_merge(_merge_ns("acme", target))

    assert rc == 0
    # Target unchanged.
    assert json.loads(target.read_text()) == {"existing": {}}


def test_merge_empty_parts_dir_is_noop(tmp_path):
    target = tmp_path / "acme.shapes.json"
    target.write_text('{"existing": {}}')
    _parts_dir(target).mkdir()

    rc = _cmd_merge(_merge_ns("acme", target))

    assert rc == 0
    assert json.loads(target.read_text()) == {"existing": {}}


def test_merge_no_base_file(tmp_path):
    """Merge creates target from parts alone when no base exists."""
    target = tmp_path / "acme.shapes.json"
    _seed_parts(target, {"t1": {"source": "live"}, "t2": {"source": "live"}})

    rc = _cmd_merge(_merge_ns("acme", target))

    assert rc == 0
    result = json.loads(target.read_text())
    assert set(result.keys()) == {"t1", "t2"}


# ── subfolder support ─────────────────────────────────────────────────────────

def test_probe_part_lands_in_subfolder(tmp_path):
    """Parts dir is a sibling of --emit-shape, even when it's in a subfolder."""
    sub = tmp_path / "github"
    target = sub / "github.shapes.json"

    part = _parts_dir(target) / "get_repo.json"
    _atomic_write_text(part, json.dumps({"get_repo": {"source": "live"}}))

    # Parts must be under sub/, not tmp_path/.
    assert part.parent == sub / "github.shapes.json.parts"
    assert part.exists()


def test_merge_subfolder_out(tmp_path):
    """merge --out sub/srv.shapes.json finds parts in that subfolder, writes there."""
    sub = tmp_path / "github"
    target = sub / "github.shapes.json"

    _seed_parts(target, {"get_repo": {"source": "live"}, "list_prs": {"source": "live"}})

    rc = _cmd_merge(_merge_ns("github", target))

    assert rc == 0
    # Merged file in the subfolder.
    assert target.exists()
    result = json.loads(target.read_text())
    assert set(result.keys()) == {"get_repo", "list_prs"}
    # Parts dir cleaned up inside the subfolder.
    assert not _parts_dir(target).exists()


# ── verify sidecar ────────────────────────────────────────────────────────────

def test_merge_writes_verify_sidecar(tmp_path):
    """merge emits <server>.verify.json with raw probed_args keyed by tool."""
    target = tmp_path / "acme.shapes.json"
    _seed_parts(target, {
        "get_entity": {"source": "live", "probed_args": {"id": "abc123", "type": 1}},
        "whoami": {"source": "live", "probed_args": {}},  # no-arg — must be omitted
    })

    rc = _cmd_merge(_merge_ns("acme", target))

    assert rc == 0
    verify = tmp_path / "acme.verify.json"
    assert verify.exists(), "verify sidecar must be written"
    data = json.loads(verify.read_text())
    assert data == {"get_entity": {"id": "abc123", "type": 1}}, (
        "verify.json must be keyed by tool, no-arg tools omitted"
    )


def test_merge_verify_sidecar_omits_no_arg_tools(tmp_path):
    """Tools with probed_args == {} are excluded from the sidecar."""
    target = tmp_path / "acme.shapes.json"
    _seed_parts(target, {
        "noop": {"source": "live", "probed_args": {}},
        "noop2": {"source": "live"},  # missing key entirely
    })

    _cmd_merge(_merge_ns("acme", target))

    verify = tmp_path / "acme.verify.json"
    # No non-empty probed_args → sidecar must not be created.
    assert not verify.exists(), "verify sidecar must not be created when all args are empty"


def test_merge_verify_sidecar_overlays_existing(tmp_path):
    """Partial re-probe: existing sidecar entries for un-probed tools are preserved."""
    target = tmp_path / "acme.shapes.json"
    verify = tmp_path / "acme.verify.json"

    # Simulate a prior run that produced a sidecar with tool_a.
    verify.write_text(json.dumps({"tool_a": {"owner": "prior"}}))

    # New run only re-probes tool_b.
    _seed_parts(target, {
        "tool_b": {"source": "live", "probed_args": {"repo": "kit"}},
    })

    _cmd_merge(_merge_ns("acme", target))

    data = json.loads(verify.read_text())
    assert data["tool_a"] == {"owner": "prior"}, "prior sidecar entry must be preserved"
    assert data["tool_b"] == {"repo": "kit"}, "new part must overlay sidecar"


def test_merge_verify_sidecar_from_parts_not_scrubbed_base(tmp_path):
    """Sidecar derives probed_args from parts only, not from a scrubbed base shapes.json."""
    target = tmp_path / "acme.shapes.json"

    # Base has scrubbed probed_args (post-commit state).
    base = {"get_entity": {"source": "live", "probed_args": {"id": "<example-id>"}}}
    target.write_text(json.dumps(base))

    # Part for the same tool carries the raw (pre-scrub) args.
    _seed_parts(target, {
        "get_entity": {"source": "live", "probed_args": {"id": "real-uuid-9999"}},
    })

    _cmd_merge(_merge_ns("acme", target))

    verify = tmp_path / "acme.verify.json"
    data = json.loads(verify.read_text())
    # Must be the raw part value, not the scrubbed base value.
    assert data["get_entity"] == {"id": "real-uuid-9999"}, (
        "verify sidecar must use raw part args, not the scrubbed base"
    )


def test_merge_no_parts_dir_hints_subfolder(tmp_path, capsys):
    """When no parts dir exists and --out was not passed, hint about subfolder."""
    # Simulate: user forgot --out; default target is CWD, but parts are in a subfolder.
    target = tmp_path / "github.shapes.json"  # CWD default, no parts here

    ns = SimpleNamespace(server="github", out=None, keep_parts=False)
    # Override target resolution: cmd_merge builds target from ns.out/ns.server;
    # we call it via public CLI path, accepting that it looks in CWD.
    # Instead, exercise hint by calling _cmd_merge with a namespace where out=None
    # and no parts dir exists (the CWD default).
    import os
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        rc = _cmd_merge(ns)
    finally:
        os.chdir(orig_cwd)

    captured = capsys.readouterr()
    assert rc == 0
    assert "subfolder" in captured.err, "hint about subfolder should appear in stderr"
