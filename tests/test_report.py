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
