"""eval-kit CLI — ties together verify, report, and gen-config."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ── Emoji helpers ─────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "pass": "✅",
    "fail": "❌",
    "skip": "⏭",
    "error": "⚠️",
}

_VERDICT_ICON = {
    "pass": "✅",
    "partial": "⚠️",
    "fail": "❌",
    "error": "⚠️",
}

# Prefix used by verify.py when mcpgen is absent — rendered with ⚠️ not ⏭
_DISABLED_SKIP_PREFIX = "mcpgen not installed"


# ── Subcommand implementations ────────────────────────────────────────────────


def cmd_verify(args: argparse.Namespace) -> int:
    """eval-kit verify <server>"""
    try:
        from eval_harness.manifest import get_server
        from eval_harness.verify import verify_server
    except ImportError as exc:
        print(f"ImportError: {exc}\nInstall the package first.", file=sys.stderr)
        return 1

    base_dir = Path(args.base_dir)
    manifest_path = Path(args.manifest)

    try:
        spec = get_server(args.server, path=manifest_path)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Verifying {spec.name}...")

    result = verify_server(spec, base_dir=base_dir)

    checks: dict[str, str] = result.get("checks", {})
    details: dict[str, str] = result.get("check_details", {})

    for check_name, status in checks.items():
        detail = details.get(check_name, "")
        if status == "skip" and detail.startswith(_DISABLED_SKIP_PREFIX):
            icon = "⚠️"
        else:
            icon = _STATUS_ICON.get(status, "?")
        suffix = f"  {detail}" if detail else ""
        print(f"{icon} {check_name:<14} {status}{suffix}")

    verdict = result.get("verdict", "error")
    verdict_icon = _VERDICT_ICON.get(verdict, "?")
    print(f"\nVerdict: {verdict_icon} {verdict}")

    server_dir = base_dir / spec.name
    result_json = server_dir / "result.json"
    print(f"Written: eval/{spec.name}/result.json")

    if verdict == "pass":
        return 0
    if verdict == "error":
        print(f"Warning: verify returned verdict=error for {spec.name}", file=sys.stderr)
        return 0
    # "partial" or "fail"
    return 1


def cmd_gen_config(args: argparse.Namespace) -> int:
    """eval-kit gen-config"""
    try:
        from eval_harness.gen_config import write_mcp_config
    except ImportError as exc:
        print(f"ImportError: {exc}\nInstall the package first.", file=sys.stderr)
        return 1

    out_path = write_mcp_config(
        out_path=args.out,
        manifest_path=args.manifest,
    )
    print(f"Config written: {out_path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """eval-kit report"""
    try:
        from eval_harness.report import generate_report, find_results
    except ImportError as exc:
        print(f"ImportError: {exc}\nInstall the package first.", file=sys.stderr)
        return 1

    base_dir = Path(args.base_dir)
    out_path = Path(args.out)

    # Count servers for the summary line
    result_paths = find_results(base_dir)
    n_servers = len(result_paths)

    generate_report(base_dir=base_dir, out_path=out_path, with_narrative=args.with_narrative)
    print(f"Report written: {out_path} ({n_servers} servers)")
    return 0



# ── Parser ────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval-kit",
        description="Evaluation harness CLI for mcpgen.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<subcommand>")
    sub.required = True

    # --- verify ---
    p_verify = sub.add_parser("verify", help="Verify a server's generated artifacts.")
    p_verify.add_argument("server", help="Server name (must appear in manifest).")
    p_verify.add_argument(
        "--base-dir", default="eval", metavar="DIR",
        help="Directory where <server>/ folders live (default: eval).",
    )
    p_verify.add_argument(
        "--manifest", default="servers/servers.toml", metavar="PATH",
        help="Path to servers.toml manifest (default: servers/servers.toml).",
    )

    # --- gen-config ---
    p_gen = sub.add_parser(
        "gen-config",
        help="Generate .mcp.eval.json from servers.toml (for mcpgen name resolution).",
    )
    p_gen.add_argument(
        "--manifest", default="servers/servers.toml", metavar="PATH",
        help="Path to servers.toml manifest (default: servers/servers.toml).",
    )
    p_gen.add_argument(
        "--out", default=".mcp.eval.json", metavar="PATH",
        help="Output JSON path (default: .mcp.eval.json).",
    )

    # --- report ---
    p_report = sub.add_parser("report", help="Generate EVAL_REPORT.md from result.json files.")
    p_report.add_argument(
        "--base-dir", default="eval", metavar="DIR",
        help="Directory where <server>/ folders live (default: eval).",
    )
    p_report.add_argument(
        "--out", default="doc/EVAL_REPORT.md", metavar="PATH",
        help="Output path for the report (default: doc/EVAL_REPORT.md).",
    )
    p_report.add_argument(
        "--with-narrative",
        action="store_true",
        default=False,
        help="Splice per-server narrative.md and _synthesis.md fragments into the report.",
    )

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "verify": cmd_verify,
        "report": cmd_report,
        "gen-config": cmd_gen_config,
    }

    handler = dispatch[args.command]
    code = handler(args)
    sys.exit(code)
