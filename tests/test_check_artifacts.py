"""Tests for eval_harness.check_artifacts — the committed-artifact integrity gate."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval_harness.check_artifacts import (
    check_artifacts,
    check_file_set,
    check_manifest,
    check_report_current,
    check_stamps,
    tracked_eval_files,
)
from eval_harness.report import generate_report

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RESULT = {
    "server": "time",
    "transport": "stdio",
    "auth": "none",
    "checks": {
        "ast": "pass",
        "signatures": "pass",
        "idempotency": "pass",
        "pii": "pass",
        "roundtrip": "pass",
    },
    "check_details": {},
    "modes_hit": ["A"],
    "verdict": "pass",
    "versions": {
        "engine": "0.9.0.dev1",
        "skill_ref": "v0.0.4-161-gbf232ef",
        "skill_path": "~/src/mcp-client-kit",
    },
}

MANIFEST = """\
[[servers]]
name = "time"
transport = "stdio"
launch = "uvx mcp-server-time"
auth = "none"
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A miniature repo whose one committed server folder satisfies every check."""
    server_dir = tmp_path / "eval" / "time"
    server_dir.mkdir(parents=True)
    (server_dir / "time.py").write_text("# wrapper\n")
    (server_dir / "time.shapes.json").write_text("{}\n")
    (server_dir / "run.py").write_text("# runner\n")
    (server_dir / "session-overview.md").write_text("# time\n")
    (server_dir / "result.json").write_text(json.dumps(RESULT, indent=2))

    (tmp_path / "servers").mkdir()
    (tmp_path / "servers" / "servers.toml").write_text(MANIFEST)

    (tmp_path / "doc").mkdir()
    generate_report(
        base_dir=tmp_path / "eval",
        out_path=tmp_path / "doc" / "EVAL_REPORT.md",
        with_narrative=False,
    )

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    return tmp_path


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_intact_repo_has_no_violations(repo: Path) -> None:
    assert check_artifacts(repo) == []


def test_tracked_eval_files_reads_git_not_the_filesystem(repo: Path) -> None:
    """An untracked local artifact beside the committed five stays invisible."""
    (repo / "eval" / "time" / "time.verify.json").write_text("{}\n")
    (repo / "eval" / "time" / "narrative.md").write_text("prose\n")

    assert tracked_eval_files(repo) == {
        "time": {
            "time.py",
            "time.shapes.json",
            "run.py",
            "session-overview.md",
            "result.json",
        }
    }
    assert check_artifacts(repo) == []


# ---------------------------------------------------------------------------
# file_set
# ---------------------------------------------------------------------------


def test_committed_local_only_file_is_a_violation(repo: Path) -> None:
    (repo / "eval" / "time" / "session-analyzer.md").write_text("raw\n")
    _git(repo, "add", "-f", "eval/time/session-analyzer.md")

    violations = check_file_set(tracked_eval_files(repo))

    assert [v.check for v in violations] == ["file_set"]
    assert "session-analyzer.md is committed but must stay local" in violations[0].message


def test_missing_committed_file_is_a_violation(repo: Path) -> None:
    _git(repo, "rm", "-q", "--cached", "eval/time/run.py")

    violations = check_file_set(tracked_eval_files(repo))

    assert [v.check for v in violations] == ["file_set"]
    assert "run.py is not committed" in violations[0].message


def test_file_nested_below_the_server_folder_is_a_violation(repo: Path) -> None:
    nested = repo / "eval" / "time" / "extra"
    nested.mkdir()
    (nested / "notes.md").write_text("x\n")
    _git(repo, "add", "-f", "eval/time/extra/notes.md")

    messages = [v.message for v in check_file_set(tracked_eval_files(repo))]

    assert any("notes.md is committed but must stay local" in m for m in messages)


# ---------------------------------------------------------------------------
# stamp
# ---------------------------------------------------------------------------


def _rewrite_result(repo: Path, **version_changes: str) -> None:
    """Rewrite the committed result.json with an amended versions block."""
    data = dict(RESULT)
    data["versions"] = {**RESULT["versions"], **version_changes}  # type: ignore[dict-item]
    (repo / "eval" / "time" / "result.json").write_text(json.dumps(data, indent=2))


def test_home_path_in_result_json_is_a_violation(repo: Path) -> None:
    _rewrite_result(repo, skill_path="/Users/someone/src/mcp-client-kit")

    violations = check_stamps(repo, tracked_eval_files(repo), "eval")

    assert [v.check for v in violations] == ["stamp"]
    assert "contains a home path" in violations[0].message


def test_missing_versions_block_is_a_violation(repo: Path) -> None:
    data = {k: v for k, v in RESULT.items() if k != "versions"}
    (repo / "eval" / "time" / "result.json").write_text(json.dumps(data, indent=2))

    violations = check_stamps(repo, tracked_eval_files(repo), "eval")

    assert [v.check for v in violations] == ["stamp"]
    assert "no versions block" in violations[0].message


def test_empty_version_field_is_a_violation(repo: Path) -> None:
    _rewrite_result(repo, skill_ref="")

    violations = check_stamps(repo, tracked_eval_files(repo), "eval")

    assert [v.check for v in violations] == ["stamp"]
    assert "versions.skill_ref is missing or empty" in violations[0].message


def test_unparseable_result_json_is_a_violation(repo: Path) -> None:
    (repo / "eval" / "time" / "result.json").write_text("{not json")

    violations = check_stamps(repo, tracked_eval_files(repo), "eval")

    assert [v.check for v in violations] == ["stamp"]
    assert "not valid JSON" in violations[0].message


def test_servers_at_different_versions_are_not_a_violation(repo: Path) -> None:
    """Mixed corpora are a normal state between reruns — report.py flags them."""
    other = repo / "eval" / "memory"
    other.mkdir()
    data = dict(RESULT, server="memory")
    data["versions"] = dict(RESULT["versions"], engine="0.10.0.dev1")  # type: ignore[arg-type]
    for name, body in {
        "memory.py": "# wrapper\n",
        "memory.shapes.json": "{}\n",
        "run.py": "# runner\n",
        "session-overview.md": "# memory\n",
        "result.json": json.dumps(data, indent=2),
    }.items():
        (other / name).write_text(body)
    _git(repo, "add", "-A")
    generate_report(
        base_dir=repo / "eval",
        out_path=repo / "doc" / "EVAL_REPORT.md",
        with_narrative=False,
    )
    _git(repo, "add", "-A")

    assert check_artifacts(repo) == []


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def test_unparseable_manifest_is_a_violation(repo: Path) -> None:
    (repo / "servers" / "servers.toml").write_text("[[servers]\nname =")

    violations = check_manifest(repo / "servers" / "servers.toml")

    assert [v.check for v in violations] == ["manifest"]
    assert "does not parse" in violations[0].message


def test_absent_manifest_is_a_violation(repo: Path) -> None:
    violations = check_manifest(repo / "servers" / "nope.toml")

    assert [v.check for v in violations] == ["manifest"]
    assert "does not exist" in violations[0].message


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_stale_report_is_a_violation(repo: Path) -> None:
    """A verdict that changed after the report was generated must be caught."""
    data = dict(RESULT, verdict="fail")
    (repo / "eval" / "time" / "result.json").write_text(json.dumps(data, indent=2))

    violations = check_report_current(repo, "eval", repo / "doc" / "EVAL_REPORT.md")

    assert [v.check for v in violations] == ["report"]
    assert "is stale" in violations[0].message


def test_absent_report_is_a_violation(repo: Path) -> None:
    violations = check_report_current(repo, "eval", repo / "doc" / "nope.md")

    assert [v.check for v in violations] == ["report"]
    assert "does not exist" in violations[0].message


def test_narrative_prose_in_the_report_is_not_staleness(repo: Path) -> None:
    """The committed report is spliced with local-only narrative; that is fine."""
    report = repo / "doc" / "EVAL_REPORT.md"
    lines = report.read_text(encoding="utf-8").splitlines()
    spliced = []
    for line in lines:
        spliced.append(line)
        if line.startswith("#"):
            spliced.append("")
            spliced.append("**Summary:** prose only a local run could produce.")
    report.write_text("\n".join(spliced) + "\n")

    assert check_report_current(repo, "eval", report) == []
