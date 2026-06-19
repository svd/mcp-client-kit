"""mcpgen CLI: generate typed wrappers from a live MCP server.

    mcpgen codegen acme --out acme.py
    mcpgen codegen acme --probe whoami --probe-args '{}'

Deterministic stub generation is the 80%; the optional --probe adds one live call
and records the observed response *shape* (not payload) — the empirical step that
distinguishes this from pure inputSchema codegen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote

from . import _bridge, codegen, discovery


async def _list_tools(
    server: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
    env: dict[str, str] | None = None,
) -> list[dict]:
    async with _bridge.session(
        server,
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        env=env,
    ) as s:
        result = await s.list_tools()
    tools = []
    for t in result.tools:
        tools.append(
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
            }
        )
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
    env: dict[str, str] | None = None,
) -> Any:
    caller = _bridge.McpBridgeCaller(
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        env=env,
    )
    raw = await caller.call(server, tool, args)
    return codegen.summarize_shape(raw)


async def _call(
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
    env: dict[str, str] | None = None,
) -> Any:
    caller = _bridge.McpBridgeCaller(
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        env=env,
    )
    return await caller.call(server, tool, args)


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


def _parse_env(ns: argparse.Namespace) -> dict[str, str] | None:
    """Parse --env KEY[=VAL] flags into a dict for the stdio subprocess.

    ``--env KEY``      forwards $KEY from the current shell environment.
    ``--env KEY=VAL``  sets KEY to VAL inline (no shell lookup).

    Returns None when no --env flags were given (or all were skipped).
    Unset keys produce a stderr warning and are skipped rather than raising.
    """
    items: list[str] = getattr(ns, "env", None) or []
    result: dict[str, str] = {}
    for item in items:
        if "=" in item:
            k, v = item.split("=", 1)
            if not k:
                print(f"[mcpgen] ⚠  --env {item!r}: empty key name; skipped", file=sys.stderr)
                continue
            result[k] = v
        elif item in os.environ:
            result[item] = os.environ[item]
        else:
            print(
                f"[mcpgen] ⚠  --env {item}: not set in environment; skipped",
                file=sys.stderr,
            )
    return result or None


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via a pid-unique temp file + os.replace.

    Safe on all OS (Windows, macOS, Linux) — os.replace is atomic when src and
    dst are on the same filesystem, which is always true here.  Using the pid in
    the temp name means concurrent probe processes for the *same* tool each get
    their own staging file; the last os.replace wins (fine — same-tool last-writer
    semantics are acceptable).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _parts_dir(target: Path) -> Path:
    """Return the parts directory for a shapes target path.

    e.g. acme.shapes.json  →  acme.shapes.json.parts/
    """
    return target.with_name(target.name + ".parts")


def _normalize_shapes(shapes: dict) -> list[str]:
    """Normalize type-annotation strings in *shapes* in-place.

    Rewrites JSON/TS-cased tokens (``any``→``Any``, ``null``→``None``, etc.)
    found in ``fields``, ``input_overrides``, and ``return_model`` values.
    Returns a list of change descriptions suitable for a stderr report.
    """
    changes: list[str] = []
    for spec in shapes.values():
        if not isinstance(spec, dict):
            continue
        for fname, ftype in list((spec.get("fields") or {}).items()):
            if isinstance(ftype, str):
                new = codegen.normalize_annotation(ftype)
                if new != ftype:
                    spec["fields"][fname] = new
                    changes.append(f"'{ftype}'→'{new}'")
        for pname, ptype in list((spec.get("input_overrides") or {}).items()):
            if isinstance(ptype, str):
                new = codegen.normalize_annotation(ptype)
                if new != ptype:
                    spec["input_overrides"][pname] = new
                    changes.append(f"'{ptype}'→'{new}'")
        rm = spec.get("return_model")
        if isinstance(rm, str):
            new = codegen.normalize_annotation(rm)
            if new != rm:
                spec["return_model"] = new
                changes.append(f"'{rm}'→'{new}'")
    return changes


def _load_shapes(ns: argparse.Namespace) -> dict | None:
    """Load the shape-spec sidecar: explicit --shapes, else <server>.shapes.json beside --out.

    Fallback: if the shapes file is absent but its .parts/ directory exists (i.e.
    probes ran but `mcpgen merge` was not yet called), merge the parts in-memory
    so `codegen` still works without requiring an explicit merge step.
    """
    path = None
    if ns.shapes:
        path = Path(ns.shapes)
    elif ns.out:
        sibling = Path(ns.out).with_name(f"{_server_stem(ns.server)}.shapes.json")
        if sibling.is_file():
            path = sibling
        else:
            # In-memory fallback: merge any parts that exist.
            parts_d = _parts_dir(sibling)
            if parts_d.is_dir():
                parts = sorted(parts_d.glob("*.json"))
                if parts:
                    skeletons = [json.loads(p.read_text()) for p in parts]
                    shapes = codegen.merge_skeletons(skeletons)
                    changes = _normalize_shapes(shapes)
                    print(
                        f"[codegen] shapes: {parts_d}/ ({len(shapes)} tool(s), "
                        "in-memory merge — run `mcpgen merge` to persist)",
                        file=sys.stderr,
                    )
                    if changes:
                        print(
                            f"[codegen] shapes: normalized {len(changes)} type string(s): " + ", ".join(changes),
                            file=sys.stderr,
                        )
                    return shapes
    if path is None:
        return None
    shapes = json.loads(path.read_text())
    changes = _normalize_shapes(shapes)
    print(f"[codegen] shapes: {path} ({len(shapes)} tool(s))", file=sys.stderr)
    if changes:
        print(
            f"[codegen] shapes: normalized {len(changes)} type string(s): " + ", ".join(changes),
            file=sys.stderr,
        )
    return shapes


def _cmd_codegen(ns: argparse.Namespace) -> int:
    cmd = getattr(ns, "stdio", None)
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        env=_parse_env(ns),
    )
    try:
        tools = asyncio.run(_list_tools(ns.server, cmd=cmd, **conn))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[codegen] error: {exc}", file=sys.stderr)
        return 1
    print(f"[codegen] {ns.server}: {len(tools)} tools", file=sys.stderr)

    shapes = _load_shapes(ns)

    probe_note = ""
    if ns.probe:
        args = json.loads(ns.probe_args) if ns.probe_args else {}
        print(f"[codegen] probing {ns.probe}({args}) …", file=sys.stderr)
        try:
            shape = asyncio.run(_probe(ns.server, ns.probe, args, cmd=cmd, **conn))
        except (FileNotFoundError, ValueError) as exc:
            print(f"[codegen] error: {exc}", file=sys.stderr)
            return 1
        shape_json = json.dumps(shape, indent=2)
        probe_note = f"\nObserved response shape of {ns.probe!r} (keys/types/nesting only):\n" + shape_json

    source = codegen.render_module(
        ns.server, tools, shapes=shapes, probe_note=probe_note, embed_schema=getattr(ns, "embed_schema", False)
    )
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

    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        env=_parse_env(ns),
    )
    shapes = []
    for i, args in enumerate(args_list):
        print(f"[probe]   [{i + 1}/{n}] args={args}", file=sys.stderr)
        # one session per probe (prototype); pooling is out of scope
        try:
            shape = asyncio.run(_probe(ns.server, ns.tool, args, cmd=cmd, **conn))
        except (FileNotFoundError, ValueError) as exc:
            print(f"[probe] error: {exc}", file=sys.stderr)
            return 1
        shapes.append(shape)

    skeleton = codegen.probe_skeleton(ns.tool, args_list, shapes)
    out = json.dumps(skeleton, indent=2)
    if ns.emit_shape:
        target = Path(ns.emit_shape)
        parts_d = _parts_dir(target)
        part = parts_d / (_url_quote(ns.tool, safe="") + ".json")
        _atomic_write_text(part, out + "\n")
        print(f"[probe] wrote part {part}", file=sys.stderr)
        print(f"[probe] run `mcpgen merge {ns.server}` to consolidate into {target}", file=sys.stderr)
    else:
        sys.stdout.write(out + "\n")

    try:
        _DISCRIMINATOR_KEYS = {"entitytype", "type", "kind", "category", "entity_type", "objecttype", "resourcetype"}
        all_keys: set[str] = set().union(*(a.keys() for a in args_list))
        for key in sorted(all_keys):
            # Skip non-hashable values (lists, dicts) — discriminators are scalars by definition.
            values = {
                args[key]
                for args in args_list
                if key in args and isinstance(args[key], (str, int, float, bool, type(None)))
            }
            if len(values) == 1 and key.lower() in _DISCRIMINATOR_KEYS:
                val = next(iter(values))
                print(f"[probe] ⚠  {key} probed as {val!r} only — response shape is variant-specific.", file=sys.stderr)
                print(
                    "[probe]    Do NOT emit a single-variant model. Probe every value or use a base model (SKILL step 4).",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(f"[probe] ⚠  discriminator advisory skipped ({exc})", file=sys.stderr)

    return 0


def _cmd_call(ns: argparse.Namespace) -> int:
    """Make one live tool call and write the raw parsed payload to --out.

    Unlike `probe`, this preserves the full response — values, ids, etc.
    Use it for bootstrapping (e.g. call a no-arg whoami first to get real ids)
    or inspecting a tool's actual output.  The --out file is required to avoid
    flooding model context with large payloads; use a *.probe-raw.json name
    so it stays git-ignored.
    """
    cmd = getattr(ns, "stdio", None)
    try:
        args: dict = json.loads(ns.args) if ns.args else {}
    except json.JSONDecodeError as exc:
        print(f"[call] error: --args must be valid JSON ({exc})", file=sys.stderr)
        return 1
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        env=_parse_env(ns),
    )

    print(f"[call] {ns.server}.{ns.tool} (live) …", file=sys.stderr)
    try:
        raw = asyncio.run(_call(ns.server, ns.tool, args, cmd=cmd, **conn))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[call] error: {exc}", file=sys.stderr)
        return 1

    text = raw if isinstance(raw, str) else json.dumps(raw, indent=2)
    out = Path(ns.out)
    _atomic_write_text(out, text + "\n")

    kb = len(text.encode()) / 1024
    print(f"[call] wrote raw payload ({kb:.1f} KB) to {out}", file=sys.stderr)
    print(
        "[call] ⚠  raw payload may contain real ids/PII — use a *.probe-raw.json name (git-ignored).",
        file=sys.stderr,
    )
    return 0


def _cmd_merge(ns: argparse.Namespace) -> int:
    """Consolidate per-tool part files into a single <server>.shapes.json.

    Probing in parallel writes one part file per tool under
    <target>.parts/<tool>.json.  This command merges all parts into the
    committed, hand-editable shapes sidecar, preserving any tool entries that
    were NOT re-probed (i.e. already present in an existing shapes.json and
    absent from the parts dir).
    """
    target = Path(ns.out) if ns.out else Path(f"{_server_stem(ns.server)}.shapes.json")
    parts_d = _parts_dir(target)

    if not parts_d.is_dir():
        print(
            f"[merge] no parts dir {parts_d} — nothing to merge"
            + (" (probed into a subfolder? pass --out <dir>/<server>.shapes.json)" if not ns.out else ""),
            file=sys.stderr,
        )
        return 0

    parts = sorted(parts_d.glob("*.json"))
    if not parts:
        print(f"[merge] parts dir {parts_d} is empty — nothing to merge", file=sys.stderr)
        return 0

    # Load base (existing shapes.json) so hand-edited entries for un-probed
    # tools are preserved.  Parts for re-probed tools override the base.
    base: dict = {}
    if target.is_file():
        base = json.loads(target.read_text())
        print(f"[merge] base: {target} ({len(base)} tool(s))", file=sys.stderr)

    part_skeletons = [json.loads(p.read_text()) for p in parts]
    merged = codegen.merge_skeletons([base] + part_skeletons)
    _atomic_write_text(target, json.dumps(merged, indent=2) + "\n")
    print(f"[merge] wrote {target} ({len(merged)} tool(s))", file=sys.stderr)

    # Emit verify sidecar: raw probed_args from parts only (pre-scrub),
    # keyed by tool name.  Omit no-arg tools (probed_args == {}).
    # Overlay existing sidecar so partial re-probes preserve prior entries.
    stem = target.name[: -len(".shapes.json")] if target.name.endswith(".shapes.json") else target.stem
    verify_target = target.with_name(stem + ".verify.json")
    verify_map: dict = {}
    if verify_target.is_file():
        try:
            verify_map = json.loads(verify_target.read_text())
        except (OSError, json.JSONDecodeError):
            verify_map = {}
    for sk in part_skeletons:
        for tool_name, entry in sk.items():
            if isinstance(entry, dict):
                pa = entry.get("probed_args")
                if pa:  # non-empty dict or non-empty list
                    verify_map[tool_name] = pa
    if verify_map:
        _atomic_write_text(verify_target, json.dumps(verify_map, indent=2) + "\n")
        print(
            f"[merge] wrote {verify_target} ({len(verify_map)} tool(s))"
            " — ⚠  raw args/PII, git-ignored (verify sidecar)",
            file=sys.stderr,
        )

    if not ns.keep_parts:
        shutil.rmtree(parts_d)
        print(f"[merge] removed {parts_d}", file=sys.stderr)

    return 0


def _cmd_discover(ns: argparse.Namespace) -> int:
    """List MCP servers from installed agent host environments."""
    host_filter: list[str] | None = ns.host  # None or list of ids
    try:
        servers = discovery.discover_all(hosts=host_filter)
    except Exception as exc:
        print(f"[discover] error: {exc}", file=sys.stderr)
        return 1

    if ns.json:
        sys.stdout.write(json.dumps([s.as_dict(redact_env=not ns.include_env) for s in servers], indent=2) + "\n")
        return 0

    # ------------------------------------------------------------------ #
    # Human table — grouped by host                                        #
    # ------------------------------------------------------------------ #

    # Build provider display_name lookup: id -> display_name
    provider_names: dict[str, str] = {p.id: p.display_name for p in discovery.PROVIDERS}

    # Group servers by host id, preserving insertion order
    groups: dict[str, list[discovery.DiscoveredServer]] = {}
    for s in servers:
        groups.setdefault(s.host, []).append(s)

    # Determine which host ids to show (respecting filter)
    if host_filter is not None:
        host_ids = [h for h in host_filter if h in groups]
    else:
        host_ids = list(groups.keys())

    col_name = 25
    col_transport = 10
    col_scope = 18

    for hid in host_ids:
        display = provider_names.get(hid, hid)
        print(f"=== {display} ===")
        print()

        group = groups.get(hid, [])
        if not group:
            print("  (no servers found)")
            print()
            continue

        for s in group:
            # Truncate scope label at first " ("
            scope_label = s.scope or ""
            if " (" in scope_label:
                scope_label = scope_label.split(" (", 1)[0]

            status_label = s.status or ""
            name_col = s.name.ljust(col_name)
            transport_col = s.transport.ljust(col_transport)
            scope_col = scope_label.ljust(col_scope)
            print(f"  {name_col}{transport_col}{scope_col}{status_label}")

            if s.probeable:
                if s.transport == "stdio" and s.command:
                    cmd_str = s.command + (" " + " ".join(s.args) if s.args else "")
                    print(f'    → mcpgen list {s.name} --stdio "{cmd_str}"')
                elif s.transport in ("http", "sse") and s.url:
                    print(f"    → mcpgen list {s.name} --url {s.url}")
                else:
                    print(f"    (hint unavailable — transport: {s.transport})")
            else:
                if s.note:
                    print(f"  ⚠  {s.note}")

        print()

    return 0


def _cmd_list(ns: argparse.Namespace) -> int:
    """Print the tool inventory as JSON [{name, description}] for a server."""
    cmd = getattr(ns, "stdio", None)
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        env=_parse_env(ns),
    )
    try:
        tools = asyncio.run(_list_tools(ns.server, cmd=cmd, **conn))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[list] error: {exc}", file=sys.stderr)
        return 1
    if getattr(ns, "schema", False):
        out = [
            {"name": t["name"], "description": t.get("description") or "", "inputSchema": t.get("inputSchema") or {}}
            for t in tools
        ]
    else:
        out = [{"name": t["name"], "description": t.get("description") or ""} for t in tools]
    sys.stdout.write(json.dumps(out, indent=2) + "\n")

    candidates = codegen.detect_discriminators(tools)
    if candidates:
        print("[list] ⚠  discriminator candidates (response shape varies by these args):", file=sys.stderr)
        for param, tool_names in candidates.items():
            print(f"[list]   {param} → {', '.join(tool_names)}", file=sys.stderr)
        print(
            "[list]   Probe EACH value or use a base model — do NOT type from one probe. See SKILL step 4.",
            file=sys.stderr,
        )

    return 0


def _cmd_login(ns: argparse.Namespace) -> int:
    asyncio.run(
        _bridge.login(
            ns.server,
            url=ns.url,
            client_name=ns.client_name,
            config_path=ns.config,
            cred_backend=ns.cred_backend,
        )
    )
    return 0


def _cmd_migrate_creds(ns: argparse.Namespace) -> int:
    parsed_servers: list[str] | None = None
    if ns.servers:
        parsed_servers = [s.strip() for s in ns.servers.split(",") if s.strip()]
        if not parsed_servers:
            parsed_servers = None

    result = _bridge.migrate_creds(
        ns.from_backend,
        ns.to_backend,
        servers=parsed_servers,
        purge=ns.purge,
        set_default=ns.set_default,
    )

    n = result["migrated"]
    ow = result["overwritten"]
    src = result["from"]
    dst = result["to"]
    purged_note = "source purged" if result["purged"] else "source kept"
    default_note = f"; default set to {dst!r}" if result["set_default"] else ""
    print(
        f"[migrate-creds] copied {n} server(s) {src} → {dst} ({ow} overwritten); {purged_note}{default_note}",
        file=sys.stderr,
    )
    return 0


def _cmd_list_creds(ns: argparse.Namespace) -> int:
    """Print stored credentials as a human table (default) or JSON (--json)."""
    rows = _bridge.list_creds(backend=ns.cred_backend, expired_only=ns.expired)

    if ns.json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return 0

    if not rows:
        msg = "no expired credentials stored" if ns.expired else "no credentials stored"
        print(f"[list-creds] {msg}", file=sys.stderr)
        return 0

    # Human table: NAME / STATUS / EXPIRES
    col_name = max(len(r["name"]) for r in rows)
    col_name = max(col_name, 4)  # min header width "NAME"
    col_status = 9  # longest status value: "no-expiry"
    header = "NAME".ljust(col_name) + "  " + "STATUS".ljust(col_status) + "  " + "EXPIRES"
    print(header)
    print("-" * len(header))
    for r in rows:
        if r["expired"]:
            status = "expired"
        elif r["expires_at"] is None:
            status = "no-expiry"
        else:
            status = "valid"
        if r["expires_at"] is not None:
            expires = datetime.fromtimestamp(r["expires_at"]).isoformat(timespec="seconds")
        else:
            expires = "-"
        print(r["name"].ljust(col_name) + "  " + status.ljust(col_status) + "  " + expires)
    return 0


def _cmd_delete_creds(ns: argparse.Namespace) -> int:
    """Delete a single stored credential by server name."""
    if not ns.yes:
        print(
            f"[delete-creds] delete stored credential for {ns.server!r}? [y/N] ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        answer = input().strip().lower()
        if answer not in ("y", "yes"):
            print("[delete-creds] aborted", file=sys.stderr)
            return 0

    existed = _bridge.delete_cred(ns.server, backend=ns.cred_backend)
    if existed:
        print(f"[delete-creds] deleted {ns.server!r}", file=sys.stderr)
    else:
        print(f"[delete-creds] no stored credential for {ns.server!r}", file=sys.stderr)
    return 0


def _add_conn_args(p: argparse.ArgumentParser) -> None:
    """Inline server-connection args shared by all commands (override config)."""
    p.add_argument("--url", help="server URL inline; enables OAuth without a config entry")
    p.add_argument(
        "--bearer",
        metavar="TOKEN",
        help="static Bearer token for APIs that use PATs (e.g. GitHub); "
        "bypasses OAuth. Read from $GITHUB_PAT or similar — never "
        "pass a literal token on the command line.",
    )
    p.add_argument(
        "--client-name", dest="client_name", help="OAuth client_name override (shown on the server consent screen)"
    )
    p.add_argument(
        "--config", dest="config", help="servers config path; overrides $MCPGEN_SERVERS and the default search"
    )
    p.add_argument(
        "--cred-backend",
        dest="cred_backend",
        choices=["file", "keyring", "auto"],
        help="credential storage backend: file (default, hardened 0600), "
        "keyring (OS keychain; falls back to file if unavailable), "
        "or auto (keyring if detected, else file). "
        "Also: MCPGEN_CRED_BACKEND env or ~/.mcpgen/config.json 'cred_backend'.",
    )
    p.add_argument(
        "--env",
        dest="env",
        action="append",
        metavar="KEY[=VAL]",
        help="forward an env var to a --stdio server: 'KEY' reads $KEY from "
        "the shell; 'KEY=VAL' sets it inline. Repeat for multiple vars. "
        "No-op when --stdio is not used.",
    )


def main(argv: list[str] | None = None) -> int:
    from importlib.metadata import version as _pkg_version

    try:
        _version = _pkg_version("mcp-client-kit")
    except Exception:
        _version = "unknown"

    parser = argparse.ArgumentParser(prog="mcpgen")
    parser.add_argument("--version", action="version", version=f"mcpgen {_version}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cg = sub.add_parser("codegen", help="generate typed wrappers for a server")
    cg.add_argument("server", help="server name (e.g. acme) or URL")
    cg.add_argument("--out", help="output .py path (default: stdout)")
    cg.add_argument("--shapes", help="shape-spec JSON sidecar (default: <server>.shapes.json beside --out)")
    cg.add_argument("--probe", help="tool to call live and record response shape (docstring note only)")
    cg.add_argument("--probe-args", help="JSON args for --probe (default: {})")
    cg.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    cg.add_argument(
        "--embed-schema",
        action="store_true",
        dest="embed_schema",
        help="embed raw inputSchema as __schema__ attribute and Args docstring per tool",
    )
    _add_conn_args(cg)
    cg.set_defaults(func=_cmd_codegen)

    pr = sub.add_parser("probe", help="live-call a tool and emit a shape-spec skeleton")
    pr.add_argument("server", help="server name (e.g. acme) or URL")
    pr.add_argument("tool", help="tool to call live")
    pr.add_argument(
        "--args",
        action="append",
        metavar="JSON",
        help="JSON args for one probe call; repeat for multi-probe (default: {})",
    )
    pr.add_argument("--emit-shape", help="write skeleton to this path (default: stdout)")
    pr.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(pr)
    pr.set_defaults(func=_cmd_probe)

    cl = sub.add_parser("call", help="live-call a tool and write the raw payload to --out")
    cl.add_argument("server", help="server name (e.g. acme) or URL")
    cl.add_argument("tool", help="tool to call live")
    cl.add_argument("--args", metavar="JSON", default=None, help="JSON args for the tool call (default: {})")
    cl.add_argument(
        "--out", required=True, metavar="FILE", help="write raw payload here; use a *.probe-raw.json name — git-ignored"
    )
    cl.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(cl)
    cl.set_defaults(func=_cmd_call)

    mg = sub.add_parser("merge", help="consolidate per-tool probe parts into <server>.shapes.json")
    mg.add_argument("server", help="server name (e.g. acme) or URL")
    mg.add_argument(
        "--out",
        help=(
            "target shapes file (default: <server>.shapes.json in CWD). "
            "MUST match the path passed to probe --emit-shape when probing into a subfolder, "
            "e.g. --out github/github.shapes.json"
        ),
    )
    mg.add_argument(
        "--keep-parts", action="store_true", help="keep the .parts/ directory after merging (default: remove)"
    )
    mg.add_argument(
        "--config",
        dest="config",
        help="accepted for flag-surface consistency with other subcommands; "
        "has no effect on merge (merge is pure filesystem consolidation)",
    )
    mg.set_defaults(func=_cmd_merge)

    ls = sub.add_parser("list", help="list tools for a server as JSON [{name, description}]")
    ls.add_argument("server", help="server name (e.g. acme) or URL")
    ls.add_argument("--schema", action="store_true", help="include raw inputSchema JSON per tool")
    ls.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(ls)
    ls.set_defaults(func=_cmd_list)

    lg = sub.add_parser("login", help="browser OAuth login for a named server")
    lg.add_argument("server", help="server name (e.g. acme)")
    _add_conn_args(lg)
    lg.set_defaults(func=_cmd_login)

    mc = sub.add_parser(
        "migrate-creds",
        help="copy stored credentials between file and keyring backends",
    )
    mc.add_argument(
        "--from",
        dest="from_backend",
        required=True,
        choices=["file", "keyring"],
        help="source backend",
    )
    mc.add_argument(
        "--to",
        dest="to_backend",
        required=True,
        choices=["file", "keyring"],
        help="target backend",
    )
    mc.add_argument(
        "--servers",
        metavar="A,B,C",
        help="comma-separated server names to migrate (default: all stored servers)",
    )
    mc.add_argument(
        "--purge",
        action="store_true",
        help="remove migrated entries from the source after a verified copy (default: keep)",
    )
    mc.add_argument(
        "--set-default",
        dest="set_default",
        action="store_true",
        help="write cred_backend=<to> into ~/.mcpgen/config.json so future "
        "commands default to the target backend (default: leave config untouched)",
    )
    mc.set_defaults(func=_cmd_migrate_creds)

    lc = sub.add_parser("list-creds", help="list stored credentials (flags expired)")
    lc.add_argument(
        "--expired",
        action="store_true",
        help="show only expired credentials (omit valid and non-expiring entries)",
    )
    lc.add_argument(
        "--json",
        action="store_true",
        help="emit JSON array instead of a human table",
    )
    lc.add_argument(
        "--cred-backend",
        dest="cred_backend",
        choices=["file", "keyring", "auto"],
        help="credential storage backend (default: resolved from env/config, else file)",
    )
    lc.set_defaults(func=_cmd_list_creds)

    dl = sub.add_parser("delete-creds", help="delete the stored credential for one server")
    dl.add_argument("server", help="server name whose stored credential to delete")
    dl.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="skip the confirmation prompt",
    )
    dl.add_argument(
        "--cred-backend",
        dest="cred_backend",
        choices=["file", "keyring", "auto"],
        help="credential storage backend (default: resolved from env/config, else file)",
    )
    dl.set_defaults(func=_cmd_delete_creds)

    dc = sub.add_parser("discover", help="list MCP servers from installed agent hosts")
    dc.add_argument(
        "--host", action="append", dest="host", metavar="ID", help="filter to this host id (repeatable; default: all)"
    )
    dc.add_argument("--json", action="store_true", help="emit JSON array instead of human table")
    dc.add_argument(
        "--include-env",
        action="store_true",
        dest="include_env",
        help="include raw env values in JSON output (may expose secrets)",
    )
    dc.set_defaults(func=_cmd_discover)

    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
