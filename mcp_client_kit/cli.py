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


async def _list_tools(server: str) -> list[dict]:
    async with _bridge.session(server) as s:
        result = await s.list_tools()
    tools = []
    for t in result.tools:
        tools.append({
            "name": t.name,
            "description": t.description,
            "inputSchema": t.inputSchema,
        })
    return tools


async def _probe(server: str, tool: str, args: dict) -> Any:
    caller = _bridge.McpBridgeCaller()
    raw = await caller.call(server, tool, args)
    return codegen.summarize_shape(raw)


def _cmd_codegen(ns: argparse.Namespace) -> int:
    tools = asyncio.run(_list_tools(ns.server))
    print(f"[codegen] {ns.server}: {len(tools)} tools", file=sys.stderr)

    probe_note = ""
    if ns.probe:
        args = json.loads(ns.probe_args) if ns.probe_args else {}
        print(f"[codegen] probing {ns.probe}({args}) …", file=sys.stderr)
        shape = asyncio.run(_probe(ns.server, ns.probe, args))
        shape_json = json.dumps(shape, indent=2)
        probe_note = (
            f"\nObserved response shape of {ns.probe!r} (keys/types/nesting only):\n"
            + shape_json
        )

    source = codegen.render_module(ns.server, tools, probe_note=probe_note)
    if ns.out:
        Path(ns.out).write_text(source)
        print(f"[codegen] wrote {ns.out} ({len(source)} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(source)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-kit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cg = sub.add_parser("codegen", help="generate typed wrappers for a server")
    cg.add_argument("server", help="server name (e.g. radar)")
    cg.add_argument("--out", help="output .py path (default: stdout)")
    cg.add_argument("--probe", help="tool to call live and record response shape")
    cg.add_argument("--probe-args", help="JSON args for --probe (default: {})")
    cg.set_defaults(func=_cmd_codegen)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
