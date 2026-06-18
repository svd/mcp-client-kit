"""Generate EVAL_REPORT.md from result.json files produced by verify.py."""

import datetime
import json
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_results(base_dir: Path) -> list[Path]:
    """Find all <server>/result.json files under base_dir, sorted by server name."""
    results = sorted(
        base_dir.glob("*/result.json"),
        key=lambda p: p.parent.name,
    )
    return results


def load_result(result_path: Path) -> dict:
    """Load a result.json and return the parsed dict."""
    with result_path.open() as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_transport(transport: str) -> str:
    """Return display form: 'stdio' → 'stdio', 'http' → 'HTTP', 'sse' → 'SSE'."""
    mapping = {
        "stdio": "stdio",
        "http": "HTTP",
        "sse": "SSE",
    }
    return mapping.get(transport.lower(), transport)


def format_auth(auth: str) -> str:
    """Return display form: 'none' → 'none', 'oauth' → 'OAuth', 'bearer:*' → 'Bearer'."""
    if auth.lower() == "none":
        return "none"
    if auth.lower().startswith("oauth"):
        return "OAuth"
    if auth.lower().startswith("bearer"):
        return "Bearer"
    return auth


def mode_cell(modes_hit: list[str], mode: str) -> str:
    """Return '✅' if mode in modes_hit, else '—'."""
    return "✅" if mode in modes_hit else "—"


def verdict_cell(verdict: str) -> str:
    """Return '✅ pass', '⚠️ partial', '❌ fail', or '❓ unknown'."""
    mapping = {
        "pass": "✅ pass",
        "partial": "⚠️ partial",
        "fail": "❌ fail",
    }
    return mapping.get(verdict.lower(), "❓ unknown")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_MODE_LEGEND = """\
**Mode A** = all-Any stub from `tools/list` alone.
**Mode B** = single-record TypedDict with `_dig` unwrap.
**Mode C** = list TypedDict with `_dig_list` unwrap.
**Mode D** = discriminator `@overload` emission.
**Path E** = skeleton tool added; re-gen byte-identical (idempotency).
**Path F** = recursive / nested response structure (design note, not yet a runtime feature).\
"""

_TABLE_HEADER = """\
| Server | Transport | Auth | Mode A | Mode B | Mode C | Mode D | Path E | Path F | Verdict |
|---|---|---|---|---|---|---|---|---|---|"""


def _path_cell(result: dict, key: str) -> str:
    """Return ✅ if result[key] is truthy, else —."""
    return "✅" if result.get(key) else "—"


def render_matrix(results: list[dict]) -> str:
    """Render the progress matrix table as a markdown string."""
    lines = [_TABLE_HEADER]
    for r in results:
        server = r.get("server", "?")
        transport = format_transport(r.get("transport", ""))
        auth = format_auth(r.get("auth", "none"))
        modes = r.get("modes_hit", [])
        verdict = verdict_cell(r.get("verdict", "unknown"))
        path_e = _path_cell(r, "path_e")
        path_f = _path_cell(r, "path_f")
        row = (
            f"| {server} | {transport} | {auth} "
            f"| {mode_cell(modes, 'A')} | {mode_cell(modes, 'B')} "
            f"| {mode_cell(modes, 'C')} | {mode_cell(modes, 'D')} "
            f"| {path_e} | {path_f} | {verdict} |"
        )
        lines.append(row)
    return "\n".join(lines)


def _humanize_skip(detail: str) -> tuple[str, str, str] | None:
    """Map a machine skip-reason code to a human-readable (icon, label, prose) triple.

    Returns None for unmapped reasons so the caller falls back to the raw detail.
    """
    # Fixed exact-match codes
    _EXACT: dict[str, tuple[str, str]] = {
        "no_shaped_non_mutating_tool": (
            "N/A",
            "no read-only tool with a typed return to probe",
        ),
        "oauth_not_supported_in_verifier": (
            "N/A",
            "OAuth servers aren't exercised by the verifier",
        ),
        "probed_args_contain_placeholders": (
            "N/A",
            "no real probe arguments available",
        ),
        "no shapes.json found": (
            "N/A",
            "no shapes to probe",
        ),
    }
    # Inline check for "probed_args is not a dict" (dynamic message)
    if detail.startswith("probed_args is not a dict"):
        return ("⊘", "N/A", "multi-probe args not supported for live call")

    if detail in _EXACT:
        label, prose = _EXACT[detail]
        return ("⊘", label, prose)

    # Prefix-matched codes (dynamic f-string messages)
    if detail.startswith("missing_cred_"):
        var = detail[len("missing_cred_"):]
        return ("⊘", "N/A", f"credential {var} not set")
    if detail.startswith("mcpgen not installed"):
        return ("⊘", "not run", "mcpgen not installed")
    if detail.startswith("generated module has unresolvable imports"):
        return ("⊘", "not run", "generated module has unresolvable imports")
    if detail.startswith("function '") and "not found in generated module" in detail:
        # Extract function name from e.g. "function 'foo' not found in generated module namespace"
        end = detail.index("'", len("function '"))
        fn_name = detail[len("function '"):end]
        return ("⊘", "not run", f"generated function '{fn_name}' not found")
    if detail.startswith("Could not load shapes.json"):
        return ("⊘", "not run", "could not load shapes.json")

    return None


def _check_cell(status: str, detail: str) -> str:
    """Format a single check result cell."""
    if status.lower() == "skip" and detail:
        humanized = _humanize_skip(detail)
        if humanized is not None:
            icon, label, prose = humanized
            return f"{icon} {label} — {prose}"
        # Unmapped skip: keep raw detail
        return f"⏭ skip — {detail}"

    emoji_map = {
        "pass": "✅ pass",
        "fail": "❌ fail",
        "skip": "⏭ skip",
    }
    cell = emoji_map.get(status.lower(), f"❓ {status}")
    if detail and status.lower() == "fail":
        cell = f"{cell} — {detail}"
    return cell


def _load_narrative(base_dir: Path, server: str) -> str | None:
    """Return content of <base_dir>/<server>/narrative.md, or None if absent."""
    path = base_dir / server / "narrative.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _load_synthesis(base_dir: Path) -> str | None:
    """Return content of <base_dir>/_synthesis.md, or None if absent."""
    path = base_dir / "_synthesis.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def render_detail(result: dict) -> str:
    """Render per-server detail section with 5-check table."""
    server = result.get("server", "?")
    transport = format_transport(result.get("transport", ""))
    auth = format_auth(result.get("auth", "none"))

    checks = result.get("checks", {})
    details = result.get("check_details", {})

    check_names = [
        ("ast", "ast.parse"),
        ("signatures", "signatures"),
        ("idempotency", "idempotency"),
        ("pii", "pii"),
        ("roundtrip", "roundtrip"),
    ]

    lines = [
        f"### {server}",
        "",
        f"**Transport / auth:** {transport} / {auth}",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for key, label in check_names:
        status = checks.get(key, "unknown")
        detail = details.get(key, "")
        lines.append(f"| {label} | {_check_cell(status, detail)} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level generator
# ---------------------------------------------------------------------------

def generate_report(
    base_dir: Path = Path("eval"),
    out_path: Path = Path("doc/EVAL_REPORT.md"),
    with_narrative: bool = False,
) -> str:
    """Find all results, render report, write to out_path, return content."""
    timestamp = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    result_paths = find_results(base_dir)
    results = [load_result(p) for p in result_paths]

    # Header
    header_lines = [
        "# Eval report — mcp-client-kit generate-mcp-wrappers skill",
        "",
        f"Generated by `eval-kit report`. Last updated: {timestamp}",
        "",
        "---",
        "",
        "## Progress matrix",
        "",
        _MODE_LEGEND,
        "",
    ]

    if results:
        matrix_section = render_matrix(results)
    else:
        # Empty table — headers only, no rows
        matrix_section = _TABLE_HEADER

    header_lines.append(matrix_section)
    header_lines.append("")
    header_lines.append("---")
    header_lines.append("")

    # Server details
    if results:
        header_lines.append("## Server details")
        header_lines.append("")
        for r in results:
            header_lines.append(render_detail(r))
            if with_narrative:
                narrative = _load_narrative(base_dir, r.get("server", ""))
                if narrative:
                    header_lines.append("")
                    header_lines.append("**Summary:**")
                    header_lines.append("")
                    header_lines.append(narrative.strip())
            header_lines.append("")
    else:
        header_lines.append("No completed evals yet.")
        header_lines.append("")

    if with_narrative:
        synthesis = _load_synthesis(base_dir)
        if synthesis:
            header_lines.append("## Cross-server synthesis")
            header_lines.append("")
            header_lines.append(synthesis.strip())
            header_lines.append("")

    content = "\n".join(header_lines)

    # Write atomically
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".md.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(out_path)

    return content


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate eval report from result.json files.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("eval"),
        help="Directory containing <server>/result.json files (default: eval)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("doc/EVAL_REPORT.md"),
        help="Output path for the report (default: doc/EVAL_REPORT.md)",
    )
    parser.add_argument(
        "--with-narrative",
        action="store_true",
        default=False,
        help="Splice per-server narrative.md and _synthesis.md fragments into the report.",
    )
    args = parser.parse_args()

    content = generate_report(base_dir=args.base_dir, out_path=args.out, with_narrative=args.with_narrative)
    print(f"Report written to {args.out}")
