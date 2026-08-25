"""Tests for eval_harness.report — aggregate report generation."""
import json
from pathlib import Path
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from eval_harness.report import (
    find_results,
    render_matrix,
    render_detail,
    generate_report,
    format_transport,
    format_auth,
    mode_cell,
    verdict_cell,
    _check_cell,
    _humanize_skip,
    render_version_line,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_RESULT = {
    "server": "github",
    "transport": "http",
    "auth": "bearer:GITHUB_PAT",
    "checks": {
        "ast": "pass",
        "signatures": "pass",
        "idempotency": "skip",
        "pii": "pass",
        "roundtrip": "skip",
    },
    "check_details": {
        "ast": "",
        "signatures": "",
        "idempotency": "offline determinism check",
        "pii": "",
        "roundtrip": "missing_cred_GITHUB_PAT",
    },
    "modes_hit": ["A", "B", "C"],
    "verdict": "pass",
}


# ---------------------------------------------------------------------------
# format_transport
# ---------------------------------------------------------------------------


def test_format_transport() -> None:
    assert format_transport("http") == "HTTP"
    assert format_transport("sse") == "SSE"
    assert format_transport("stdio") == "stdio"


# ---------------------------------------------------------------------------
# format_auth
# ---------------------------------------------------------------------------


def test_format_auth() -> None:
    assert format_auth("none") == "none"
    assert format_auth("oauth") == "OAuth"
    assert format_auth("bearer:GITHUB_PAT") == "Bearer"


# ---------------------------------------------------------------------------
# mode_cell
# ---------------------------------------------------------------------------


def test_mode_cell() -> None:
    assert mode_cell(["A", "B"], "A") == "✅"
    assert mode_cell(["A"], "B") == "—"


# ---------------------------------------------------------------------------
# verdict_cell
# ---------------------------------------------------------------------------


def test_verdict_cell() -> None:
    assert verdict_cell("pass") == "✅ pass"
    assert verdict_cell("partial") == "⚠️ partial"
    assert verdict_cell("fail") == "❌ fail"


# ---------------------------------------------------------------------------
# render_matrix
# ---------------------------------------------------------------------------


def test_render_matrix_contains_server_row() -> None:
    """The matrix should include the server name and show ✅ for hit modes."""
    output = render_matrix([SAMPLE_RESULT])
    assert "github" in output
    # Modes A, B, C are all in modes_hit — each should render a ✅
    assert "✅" in output


# ---------------------------------------------------------------------------
# render_detail
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _humanize_skip / _check_cell — human-readable skip rendering
# ---------------------------------------------------------------------------


def test_humanize_skip_no_shaped_non_mutating_tool() -> None:
    result = _humanize_skip("no_shaped_non_mutating_tool")
    assert result is not None
    icon, label, prose = result
    assert icon == "⊘"
    assert label == "N/A"
    assert "read-only" in prose


def test_humanize_skip_oauth() -> None:
    icon, label, prose = _humanize_skip("oauth_not_supported_in_verifier")  # type: ignore[misc]
    assert label == "N/A"
    assert "OAuth" in prose


def test_humanize_skip_missing_cred() -> None:
    icon, label, prose = _humanize_skip("missing_cred_GITHUB_PAT")  # type: ignore[misc]
    assert label == "N/A"
    assert "GITHUB_PAT" in prose
    assert "credential" in prose


def test_humanize_skip_placeholders() -> None:
    icon, label, prose = _humanize_skip("probed_args_contain_placeholders")  # type: ignore[misc]
    assert label == "N/A"


def test_humanize_skip_no_shapes_found() -> None:
    icon, label, prose = _humanize_skip("no shapes.json found")  # type: ignore[misc]
    assert label == "N/A"
    assert "shapes" in prose


def test_humanize_skip_mcpgen_not_installed() -> None:
    icon, label, prose = _humanize_skip(  # type: ignore[misc]
        "mcpgen not installed — check DISABLED (install to enable)"
    )
    assert label == "not run"
    assert "mcpgen" in prose


def test_humanize_skip_unresolvable_imports() -> None:
    icon, label, prose = _humanize_skip(  # type: ignore[misc]
        "generated module has unresolvable imports: No module named 'foo'"
    )
    assert label == "not run"
    assert "unresolvable imports" in prose


def test_humanize_skip_function_not_found() -> None:
    icon, label, prose = _humanize_skip(  # type: ignore[misc]
        "function 'list_issues' not found in generated module namespace"
    )
    assert label == "not run"
    assert "list_issues" in prose


def test_humanize_skip_unknown_returns_none() -> None:
    assert _humanize_skip("offline determinism check") is None
    assert _humanize_skip("") is None
    assert _humanize_skip("some unknown reason") is None


def test_check_cell_skip_humanized() -> None:
    cell = _check_cell("skip", "no_shaped_non_mutating_tool")
    assert cell.startswith("⊘")
    assert "N/A" in cell
    assert "no_shaped_non_mutating_tool" not in cell


def test_check_cell_skip_missing_cred() -> None:
    cell = _check_cell("skip", "missing_cred_GITHUB_PAT")
    assert "⊘" in cell
    assert "GITHUB_PAT" in cell
    assert "missing_cred_" not in cell


def test_check_cell_skip_unmapped_keeps_raw() -> None:
    cell = _check_cell("skip", "offline determinism check")
    assert cell == "⏭ skip — offline determinism check"


def test_check_cell_skip_no_detail() -> None:
    assert _check_cell("skip", "") == "⏭ skip"


def test_check_cell_pass_and_fail_unchanged() -> None:
    assert _check_cell("pass", "") == "✅ pass"
    assert _check_cell("fail", "bad type") == "❌ fail — bad type"


def test_render_detail_has_check_rows() -> None:
    """Detail section must include all five check labels."""
    output = render_detail(SAMPLE_RESULT)
    assert "ast.parse" in output
    assert "signatures" in output
    assert "roundtrip" in output


# ---------------------------------------------------------------------------
# generate_report — empty base dir
# ---------------------------------------------------------------------------


def test_generate_report_empty(tmp_path: Path) -> None:
    """With no result.json files the report file is created and notes no evals yet."""
    out_file = tmp_path / "EVAL_REPORT.md"
    generate_report(tmp_path, out_file)
    assert out_file.exists(), "Report file should have been created"
    content = out_file.read_text(encoding="utf-8")
    assert "No completed evals yet." in content


# ---------------------------------------------------------------------------
# generate_report — with a result.json present
# ---------------------------------------------------------------------------


def test_generate_report_with_result(tmp_path: Path) -> None:
    """When a result.json exists the report should include server name and verdict."""
    server_dir = tmp_path / "github"
    server_dir.mkdir()
    result_file = server_dir / "result.json"
    result_file.write_text(json.dumps(SAMPLE_RESULT), encoding="utf-8")

    out_file = tmp_path / "EVAL_REPORT.md"
    generate_report(tmp_path, out_file)

    assert out_file.exists(), "Report file should have been created"
    content = out_file.read_text(encoding="utf-8")
    assert "github" in content
    assert "✅ pass" in content


# ---------------------------------------------------------------------------
# Version line
# ---------------------------------------------------------------------------


def _stamped(server: str, engine: str | None, skill_ref: str | None) -> dict:
    result = dict(SAMPLE_RESULT, server=server)
    result["versions"] = {"engine": engine, "skill_ref": skill_ref, "skill_path": None}
    return result


def test_render_version_line_uniform() -> None:
    """One engine and one skill ref across all servers → a single provenance line."""
    results = [_stamped("time", "0.7.0", "v0.7.0"), _stamped("git", "0.7.0", "v0.7.0")]
    line = render_version_line(results)
    assert line == "Engine: mcp-client-kit 0.7.0 · skill ref: v0.7.0"


def test_render_version_line_mixed_engines() -> None:
    """Servers verified against different engines must be flagged, not averaged."""
    results = [_stamped("time", "0.2.0", "v0.2.0"), _stamped("git", "0.7.0", "v0.7.0")]
    line = render_version_line(results)
    assert line.startswith("⚠️")
    assert "0.2.0" in line and "0.7.0" in line
    assert "time" in line and "git" in line


def test_render_version_line_missing_stamp() -> None:
    """Results predating version stamping report unknown rather than crashing."""
    assert render_version_line([SAMPLE_RESULT]) == "Engine: unknown · skill ref: unknown"


def test_render_version_line_no_results() -> None:
    """An empty run still renders a line."""
    assert render_version_line([]) == "Engine: unknown · skill ref: unknown"


def test_generate_report_includes_version_line(tmp_path: Path) -> None:
    """The provenance line reaches the rendered report header."""
    server_dir = tmp_path / "github"
    server_dir.mkdir()
    (server_dir / "result.json").write_text(
        json.dumps(_stamped("github", "0.7.0", "v0.7.0")), encoding="utf-8"
    )

    out_file = tmp_path / "EVAL_REPORT.md"
    generate_report(tmp_path, out_file)

    assert "Engine: mcp-client-kit 0.7.0 · skill ref: v0.7.0" in out_file.read_text(
        encoding="utf-8"
    )


def test_humanize_skip_no_shaped_tool_by_design() -> None:
    """A prose-only server reads as an expected terminal state, not a gap."""
    icon, label, prose = _humanize_skip("no_shaped_tool_by_design")  # type: ignore[misc]
    assert icon == "⊘"
    assert label == "N/A"
    assert "by design" in prose


def test_humanize_skip_only_mutating_shaped_tools() -> None:
    """Shaped-but-mutating-only reads as N/A: the verifier never calls mutating tools.

    It is a deliberate safety policy rather than a closable coverage gap, so it
    keeps the N/A label — but its prose must still differ from the by-design case,
    which claims something stronger (that no typed return exists at all).
    """
    icon, label, prose = _humanize_skip("only_mutating_shaped_tools")  # type: ignore[misc]
    assert label == "N/A"
    assert "mutating" in prose
    assert "by design" not in prose


def test_humanize_skip_probed_args_unexpected_type() -> None:
    """verify.py emits 'probed_args has unexpected type X' — it must map."""
    humanized = _humanize_skip("probed_args has unexpected type str")
    assert humanized is not None, "unmapped skip reason falls back to raw detail"


def test_humanize_skip_probe_inconclusive() -> None:
    """A quota/auth-blocked probe is a coverage gap, not a neutral N/A."""
    humanized = _humanize_skip(
        "probe_inconclusive: 2 tool(s) returned quota/auth errors — shapes unknown: a, b"
    )
    assert humanized is not None
    icon, label, prose = humanized
    assert label != "N/A", "an unestablished shape must not read as 'not applicable'"
    assert "quota" in prose or "auth" in prose


def test_humanize_skip_shapes_json_empty() -> None:
    """An empty shapes file is a gap and must not render as N/A."""
    humanized = _humanize_skip("shapes_json_empty")
    assert humanized is not None
    _icon, label, prose = humanized
    assert label != "N/A"
    assert "empty" in prose


def test_verdict_cell_renders_error() -> None:
    """verify_server now persists verdict=error, so the report must render it."""
    cell = verdict_cell("error")
    assert "unknown" not in cell, f"error verdict rendered as unknown: {cell!r}"
    assert "error" in cell


def test_detail_section_reports_error_instead_of_unknown_rows() -> None:
    """An error result must state its reason, not five '❓ unknown' check rows."""
    section = render_detail(
        {
            "server": "ghost",
            "transport": "http",
            "auth": "oauth",
            "checks": {},
            "check_details": {},
            "verdict": "error",
            "error": "no generated file found",
        }
    )
    assert "no generated file found" in section
    assert "❓ unknown" not in section
