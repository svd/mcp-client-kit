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


async def _list_tools(
    server: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
) -> list[dict]:
    async with _bridge.session(
        server, cmd=cmd, url=url, bearer=bearer, client_name=client_name,
        config_path=config_path, cred_backend=cred_backend,
    ) as s:
        result = await s.list_tools()
    tools = []
    for t in result.tools:
        tools.append({
            "name": t.name,
            "description": t.description,
            "inputSchema": t.inputSchema,
        })
    return tools


async def _probe(
    server: str,
    tool: str,
    args: dict,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
) -> Any:
    caller = _bridge.McpBridgeCaller(
        cmd=cmd, url=url, bearer=bearer, client_name=client_name, config_path=config_path,
        cred_backend=cred_backend,
    )
    raw = await caller.call(server, tool, args)
    return codegen.summarize_shape(raw)


def _server_stem(server: str) -> str:
    """Return a filesystem-safe stem for a server identifier (name or URL)."""
    if server.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        parsed = urlparse(server)
        # e.g. "mcp.deepwiki.com" → "mcp.deepwiki.com"
        stem = parsed.netloc or parsed.path.strip("/").replace("/", "_")
    else:
        stem = server
    return stem


def _load_shapes(ns: argparse.Namespace) -> dict | None:
    """Load the shape-spec sidecar: explicit --shapes, else <server>.shapes.json beside --out."""
    path = None
    if ns.shapes:
        path = Path(ns.shapes)
    elif ns.out:
        sibling = Path(ns.out).with_name(f"{_server_stem(ns.server)}.shapes.json")
        if sibling.is_file():
            path = sibling
    if path is None:
        return None
    shapes = json.loads(path.read_text())
    print(f"[codegen] shapes: {path} ({len(shapes)} tool(s))", file=sys.stderr)
    return shapes


def _cmd_codegen(ns: argparse.Namespace) -> int:
    cmd = getattr(ns, "stdio", None)
    conn = dict(url=ns.url, bearer=ns.bearer, client_name=ns.client_name, config_path=ns.config,
                cred_backend=ns.cred_backend)
    tools = asyncio.run(_list_tools(ns.server, cmd=cmd, **conn))
    print(f"[codegen] {ns.server}: {len(tools)} tools", file=sys.stderr)

    shapes = _load_shapes(ns)

    probe_note = ""
    if ns.probe:
        args = json.loads(ns.probe_args) if ns.probe_args else {}
        print(f"[codegen] probing {ns.probe}({args}) …", file=sys.stderr)
        shape = asyncio.run(_probe(ns.server, ns.probe, args, cmd=cmd, **conn))
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
    """Run one or more live calls and emit a merged shape-spec SKELETON.

    Pass --args once for a single probe (original behaviour, byte-stable).
    Pass --args multiple times for multi-probe mode: each call is made in
    sequence and the observed shapes are deep-merged so the skeleton reflects
    the union of all responses (nullable fields, key-presence variance, etc.).

    The deterministic part: top-level scalars → fields; unwrap, types, and
    deep-nest decisions are the skill's judgment (see SKILL.md step 4).
    """
    cmd = getattr(ns, "stdio", None)
    raw_args_list: list[str] = ns.args or []
    args_list: list[dict] = [json.loads(a) for a in raw_args_list] if raw_args_list else [{}]
    n = len(args_list)
    print(f"[probe] {ns.server}.{ns.tool} ({n} probe(s)) …", file=sys.stderr)

    conn = dict(url=ns.url, bearer=ns.bearer, client_name=ns.client_name, config_path=ns.config,
                cred_backend=ns.cred_backend)
    shapes = []
    for i, args in enumerate(args_list):
        print(f"[probe]   [{i + 1}/{n}] args={args}", file=sys.stderr)
        # one session per probe (prototype); pooling is out of scope
        shape = asyncio.run(_probe(ns.server, ns.tool, args, cmd=cmd, **conn))
        shapes.append(shape)

    skeleton = codegen.probe_skeleton(ns.tool, args_list, shapes)
    out = json.dumps(skeleton, indent=2)
    if ns.emit_shape:
        Path(ns.emit_shape).write_text(out + "\n")
        print(f"[probe] wrote skeleton {ns.emit_shape}", file=sys.stderr)
    else:
        sys.stdout.write(out + "\n")
    return 0


def _cmd_list(ns: argparse.Namespace) -> int:
    """Print the tool inventory as JSON [{name, description}] for a server."""
    cmd = getattr(ns, "stdio", None)
    conn = dict(url=ns.url, bearer=ns.bearer, client_name=ns.client_name, config_path=ns.config,
                cred_backend=ns.cred_backend)
    tools = asyncio.run(_list_tools(ns.server, cmd=cmd, **conn))
    out = [{"name": t["name"], "description": t.get("description") or ""} for t in tools]
    sys.stdout.write(json.dumps(out, indent=2) + "\n")
    return 0


def _cmd_login(ns: argparse.Namespace) -> int:
    asyncio.run(
        _bridge.login(
            ns.server, url=ns.url, client_name=ns.client_name, config_path=ns.config,
            cred_backend=ns.cred_backend,
        )
    )
    return 0


def _add_conn_args(p: argparse.ArgumentParser) -> None:
    """Inline server-connection args shared by all commands (override config)."""
    p.add_argument("--url", help="server URL inline; enables OAuth without a config entry")
    p.add_argument("--bearer", metavar="TOKEN",
                   help="static Bearer token for APIs that use PATs (e.g. GitHub); "
                        "bypasses OAuth. Read from $GITHUB_PAT or similar — never "
                        "pass a literal token on the command line.")
    p.add_argument("--client-name", dest="client_name",
                   help="OAuth client_name override (shown on the server consent screen)")
    p.add_argument("--config", dest="config",
                   help="servers config path; overrides $MCP_KIT_SERVERS and the default search")
    p.add_argument("--cred-backend", dest="cred_backend", choices=["file", "keyring", "auto"],
                   help="credential storage backend: file (default, hardened 0600), "
                        "keyring (OS keychain; falls back to file if unavailable), "
                        "or auto (keyring if detected, else file). "
                        "Also: MCP_KIT_CRED_BACKEND env or ~/.mcp-client-kit/config.json 'cred_backend'.")


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
    _add_conn_args(cg)
    cg.set_defaults(func=_cmd_codegen)

    pr = sub.add_parser("probe", help="live-call a tool and emit a shape-spec skeleton")
    pr.add_argument("server", help="server name (e.g. radar) or URL")
    pr.add_argument("tool", help="tool to call live")
    pr.add_argument("--args", action="append", metavar="JSON",
                    help="JSON args for one probe call; repeat for multi-probe (default: {})")
    pr.add_argument("--emit-shape", help="write skeleton to this path (default: stdout)")
    pr.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(pr)
    pr.set_defaults(func=_cmd_probe)

    ls = sub.add_parser("list", help="list tools for a server as JSON [{name, description}]")
    ls.add_argument("server", help="server name (e.g. radar) or URL")
    ls.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(ls)
    ls.set_defaults(func=_cmd_list)

    lg = sub.add_parser("login", help="browser OAuth login for a named server")
    lg.add_argument("server", help="server name (e.g. radar)")
    _add_conn_args(lg)
    lg.set_defaults(func=_cmd_login)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
