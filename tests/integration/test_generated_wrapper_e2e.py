"""End-to-end: generated wrappers invoke a real MCP server.

Covers the full documented lifecycle — codegen, probe, merge, hand-set shape,
regenerate to a TypedDict — plus the reused-session execution block.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from mcpgen import McpBridgeCaller
from mcpgen.cli import main

# Known product defect, pinned here rather than papered over.
#
# An MCP server returning a list serializes it as one content block per element
# (FastMCP does; the spec allows it generally). `_bridge.parse()` reads only
# `content_items[0]` and never consults `result.structuredContent`, so a
# generated wrapper for a list-returning tool silently returns just the first
# element. Raw capture for `list_records(limit=2)`:
#
#     content: [{"id": 1, ...}, {"id": 2, ...}]        <- two text blocks
#     structuredContent: {"result": [ ...both... ]}    <- ignored
#     parse() -> {"id": 1, "name": "record-1"}         <- second record lost
#
# The test below asserts the correct result and is marked xfail(strict=True):
# when `parse()` learns to reassemble multi-block results, it starts passing and
# strict mode fails the suite, prompting removal of this marker.
_LIST_TRUNCATION = (
    "product bug: parse() reads content[0] only, so multi-block list results lose every element but the first"
)


def _load(path: Path, name: str):
    """Import a generated module from an arbitrary path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_wrapper_calls_the_real_tool(tmp_path, stdio_cmd):
    out = tmp_path / "demo_gen.py"
    assert main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)]) == 0
    module = _load(out, "demo_gen")

    caller = McpBridgeCaller(cmd=stdio_cmd)
    greeting = asyncio.run(module.greet(caller, name="Grace"))
    total = asyncio.run(module.add(caller, a=40, b=2))

    assert greeting == {"message": "Hello, Grace.", "length": 5}
    assert total == 42


@pytest.mark.xfail(strict=True, reason=_LIST_TRUNCATION)
def test_generated_wrapper_returns_a_list_of_records(tmp_path, stdio_cmd):
    out = tmp_path / "demo_list.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    module = _load(out, "demo_list")

    records = asyncio.run(module.list_records(McpBridgeCaller(cmd=stdio_cmd), limit=2))
    assert records == [{"id": 1, "name": "record-1"}, {"id": 2, "name": "record-2"}]


def test_probe_merge_shape_regenerate_yields_a_typed_dict(tmp_path, stdio_cmd):
    """The documented lifecycle: Any return by default, TypedDict after shaping."""
    out = tmp_path / "demo_typed.py"
    shapes = tmp_path / "demo.shapes.json"

    # 1. codegen — untyped return
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    assert "-> Any" in out.read_text()

    # 2. probe — observe the real response shape
    assert (
        main(
            [
                "probe",
                "demo",
                "greet",
                "--stdio",
                stdio_cmd,
                "--args",
                json.dumps({"name": "Grace"}),
                "--emit-shape",
                str(shapes),
            ]
        )
        == 0
    )

    # 3. merge — consolidate the probe parts into the sidecar
    assert main(["merge", "demo", "--out", str(shapes)]) == 0
    skeleton = json.loads(shapes.read_text())
    assert "greet" in skeleton

    # 4. the judgment pass — hand-set the output model, as the skill would
    skeleton["greet"]["return_model"] = "Greeting"
    skeleton["greet"]["fields"] = {"message": "str", "length": "int"}
    shapes.write_text(json.dumps(skeleton, indent=2))

    # 5. regenerate — now typed
    assert main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out), "--shapes", str(shapes)]) == 0
    source = out.read_text()
    assert "class Greeting(TypedDict" in source
    assert "-> Greeting" in source

    # and it still calls the real server correctly
    module = _load(out, "demo_typed")
    assert asyncio.run(module.greet(McpBridgeCaller(cmd=stdio_cmd), name="Grace")) == {
        "message": "Hello, Grace.",
        "length": 5,
    }


def test_two_calls_share_one_connection_block(tmp_path, stdio_cmd):
    """Acceptance: a real stdio fixture calls two generated functions in one
    connection lifecycle.

    The plan's third call was ``list_records``; it is ``styled`` here so this
    acceptance test fails only if the connection block is broken, never because
    of the unrelated multi-block truncation defect described at the top of this
    module. Three distinct tools still share one block.
    """
    out = tmp_path / "demo_block.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    module = _load(out, "demo_block")

    async def run():
        caller = McpBridgeCaller(cmd=stdio_cmd)
        async with caller.connected():
            greeting = await module.greet(caller, name="Grace")
            total = await module.add(caller, a=40, b=2)
            formal = await module.styled(caller, name="Grace", style="formal")
        return greeting, total, formal

    greeting, total, formal = asyncio.run(run())
    assert greeting == {"message": "Hello, Grace.", "length": 5}
    assert total == 42
    assert formal == "Good day, Grace."


def test_connection_block_starts_one_subprocess(tmp_path, stdio_cmd):
    """The subprocess must start once per block, not once per call."""
    import mcpgen._bridge as bridge

    out = tmp_path / "demo_procs.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    module = _load(out, "demo_procs")

    starts = {"n": 0}
    real_stdio_session = bridge._stdio_session

    def counting(*args, **kwargs):
        starts["n"] += 1
        return real_stdio_session(*args, **kwargs)

    async def run():
        caller = McpBridgeCaller(cmd=stdio_cmd)
        async with caller.connected():
            await module.greet(caller, name="Grace")
            await module.add(caller, a=1, b=1)
            await module.add(caller, a=2, b=2)

    bridge._stdio_session = counting
    try:
        asyncio.run(run())
    finally:
        bridge._stdio_session = real_stdio_session

    assert starts["n"] == 1


def test_concurrent_calls_in_one_block_against_the_real_server(tmp_path, stdio_cmd):
    """The anyio cancel-scope path, exercised for real: a session opened from a
    gather child and closed by the parent must not raise."""
    out = tmp_path / "demo_conc.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    module = _load(out, "demo_conc")

    async def run():
        caller = McpBridgeCaller(cmd=stdio_cmd)
        async with caller.connected():
            return await asyncio.gather(
                module.add(caller, a=1, b=1),
                module.add(caller, a=2, b=2),
                module.add(caller, a=3, b=3),
            )

    assert asyncio.run(run()) == [2, 4, 6]


def test_one_shot_calls_still_work_against_the_real_server(tmp_path, stdio_cmd):
    """Regression guard: no block, no change in behaviour."""
    out = tmp_path / "demo_oneshot.py"
    main(["codegen", "demo", "--stdio", stdio_cmd, "--out", str(out)])
    module = _load(out, "demo_oneshot")

    caller = McpBridgeCaller(cmd=stdio_cmd)
    assert asyncio.run(module.add(caller, a=1, b=1)) == 2
    assert asyncio.run(module.add(caller, a=2, b=2)) == 4
