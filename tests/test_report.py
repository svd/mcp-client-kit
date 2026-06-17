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
