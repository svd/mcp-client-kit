"""mcp-kit CLI: generate typed wrappers from a live MCP server.

    mcp-kit codegen radar --out radar.py
    mcp-kit codegen radar --probe whoami --probe-args '{}'

Deterministic stub generation is the 80%; the optional --probe adds one live call
and records the observed response *shape* (not payload) — the empirical step that
distinguishes this from pure inputSchema codegen.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from . import _bridge, codegen


async def _list_tools(server: str, *, cmd: str | None = None) -> list[dict]:
    async with _bridge.session(server, cmd=cmd) as s:
        result = await s.list_tools()
    tools = []
    for t in result.tools:
        tools.append({
            "name": t.name,
            "description": t.description,
            "inputSchema": t.inputSchema,
        })
    return tools


async def _probe(server: str, tool: str, args: dict, *, cmd: str | None = None) -> Any:
    caller = _bridge.McpBridgeCaller(cmd=cmd)
    raw = await caller.call(server, tool, args)
    return codegen.summarize_shape(raw)


def _load_shapes(ns: argparse.Namespace) -> dict | None:
    """Load the shape-spec sidecar: explicit --shapes, else <server>.shapes.json beside --out."""
    path = None
    if ns.shapes:
        path = Path(ns.shapes)
    elif ns.out:
        sibling = Path(ns.out).with_name(f"{ns.server}.shapes.json")
        if sibling.is_file():
            path = sibling
    if path is None:
        return None
    shapes = json.loads(path.read_text())
    print(f"[codegen] shapes: {path} ({len(shapes)} tool(s))", file=sys.stderr)
    return shapes


def _cmd_codegen(ns: argparse.Namespace) -> int:
    cmd = getattr(ns, "stdio", None)
    tools = asyncio.run(_list_tools(ns.server, cmd=cmd))
    print(f"[codegen] {ns.server}: {len(tools)} tools", file=sys.stderr)

    shapes = _load_shapes(ns)

    probe_note = ""
    if ns.probe:
        args = json.loads(ns.probe_args) if ns.probe_args else {}
        print(f"[codegen] probing {ns.probe}({args}) …", file=sys.stderr)
        shape = asyncio.run(_probe(ns.server, ns.probe, args, cmd=cmd))
        shape_json = json.dumps(shape, indent=2)
        probe_note = (
            f"\nObserved response shape of {ns.probe!r} (keys/types/nesting only):\n"
            + shape_json
        )

    source = codegen.render_module(ns.server, tools, shapes=shapes, probe_note=probe_note)
    if ns.out:
        Path(ns.out).write_text(source)
        print(f"[codegen] wrote {ns.out} ({len(source)} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(source)
    return 0


def _cmd_probe(ns: argparse.Namespace) -> int:
    """Run one live call and emit a shape-spec SKELETON for the skill to edit.

    The deterministic part: dump the observed top-level shape as a `fields` map
    with `unwrap: []`. The judgment part (set unwrap, fix types, drop deep nests)
    is the skill's — see skills/generate-mcp-wrappers/SKILL.md.
    """
    cmd = getattr(ns, "stdio", None)
    args = json.loads(ns.args) if ns.args else {}
    print(f"[probe] {ns.server}.{ns.tool}({args}) …", file=sys.stderr)
    shape = asyncio.run(_probe(ns.server, ns.tool, args, cmd=cmd))

    # Only top-level scalars become skeleton fields; nested dicts/lists are left
    # out (skill decides whether to unwrap to them or keep them as Any).
    fields = {k: v for k, v in shape.items() if isinstance(v, str)} if isinstance(shape, dict) else {}
    skeleton = {
        ns.tool: {
            "unwrap": [],
            "return_model": None,
            "input_overrides": {},
            "fields": fields,
            "source": "live",
            "probed_args": args,
            "_observed_shape": shape,
        }
    }
    out = json.dumps(skeleton, indent=2)
    if ns.emit_shape:
        Path(ns.emit_shape).write_text(out + "\n")
        print(f"[probe] wrote skeleton {ns.emit_shape}", file=sys.stderr)
    else:
        sys.stdout.write(out + "\n")
    return 0


def _cmd_login(ns: argparse.Namespace) -> int:
    asyncio.run(_bridge.login(ns.server))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-kit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cg = sub.add_parser("codegen", help="generate typed wrappers for a server")
    cg.add_argument("server", help="server name (e.g. radar) or URL")
    cg.add_argument("--out", help="output .py path (default: stdout)")
    cg.add_argument("--shapes", help="shape-spec JSON sidecar (default: <server>.shapes.json beside --out)")
    cg.add_argument("--probe", help="tool to call live and record response shape (docstring note only)")
    cg.add_argument("--probe-args", help="JSON args for --probe (default: {})")
    cg.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    cg.set_defaults(func=_cmd_codegen)

    pr = sub.add_parser("probe", help="live-call a tool and emit a shape-spec skeleton")
    pr.add_argument("server", help="server name (e.g. radar) or URL")
    pr.add_argument("tool", help="tool to call live")
    pr.add_argument("--args", help="JSON args for the call (default: {})")
    pr.add_argument("--emit-shape", help="write skeleton to this path (default: stdout)")
    pr.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    pr.set_defaults(func=_cmd_probe)

    lg = sub.add_parser("login", help="browser OAuth login for a named server")
    lg.add_argument("server", help="server name (e.g. radar)")
    lg.set_defaults(func=_cmd_login)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
