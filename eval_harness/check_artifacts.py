"""Integrity gate over the committed eval artifacts.

Each `eval/<server>/` folder is the faithful record of one skill run, and the
repo states four things about what may be committed. None of them is enforced by
`verify.py`, which judges a single run's output rather than what lands in git.
This module is that missing check, written so CI can run it on a bare clone: it
reads the *tracked* file list, never the working tree, so local-only artifacts
(`narrative.md`, `<server>.verify.json`, `<server>.mcpgen.json`) are invisible to
it by construction rather than by an exclusion list that would drift.

The checks:

- `file_set`    — every server folder commits exactly the five allowed files.
- `stamp`       — every `result.json` carries a well-formed `versions` block and
                  no home path, since it is committed and must not name a user.
- `manifest`    — `servers/servers.toml` still parses.
- `report`      — `doc/EVAL_REPORT.md` is not stale with respect to the
                  committed `result.json` files.

Deliberately absent: any assertion that all servers ran at one engine or skill
version. Mixed corpora are a normal state between incremental reruns, and
`report.py` already flags them in the report header.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Substrings that betray a machine-specific home directory in a committed file.
_HOME_PATH_MARKERS = ("/Users/", "/home/", "C:\\Users\\", "C:/Users/")

# The report stamps its own generation time, so it differs on every run.
_VOLATILE_REPORT_MARKER = "Last updated:"

_REQUIRED_VERSION_FIELDS = ("engine", "skill_ref", "skill_path")


@dataclass(frozen=True)
class Violation:
    """One failed expectation, named by the check that raised it."""

    check: str
    message: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.message}"


def tracked_eval_files(repo_root: Path, base_dir: str = "eval") -> dict[str, set[str]]:
    """Map each committed server folder to the set of filenames tracked in it.

    Reads git rather than the filesystem: the invariant is about what is
    committed, and `eval*/` is gitignored precisely so that untracked run
    artifacts can sit beside the tracked five.
    """
    proc = subprocess.run(
        ["git", "ls-files", "-z", f"{base_dir}/"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    folders: dict[str, set[str]] = {}
    for path in proc.stdout.split("\0"):
        if not path:
            continue
        parts = Path(path).parts
        if len(parts) != 3:
            # Nested deeper than <base>/<server>/<file> — reported by check_file_set.
            folders.setdefault("/".join(parts[1:-1]), set()).add(parts[-1])
            continue
        folders.setdefault(parts[1], set()).add(parts[2])
    return folders


def check_file_set(folders: dict[str, set[str]]) -> list[Violation]:
    """Every server folder commits exactly its five allowed files."""
    violations: list[Violation] = []
    for server in sorted(folders):
        expected = {
            f"{server}.py",
            f"{server}.shapes.json",
            "run.py",
            "session-overview.md",
            "result.json",
        }
        actual = folders[server]
        for extra in sorted(actual - expected):
            violations.append(
                Violation("file_set", f"{server}: {extra} is committed but must stay local")
            )
        for missing in sorted(expected - actual):
            violations.append(Violation("file_set", f"{server}: {missing} is not committed"))
    return violations


def check_stamps(repo_root: Path, folders: dict[str, set[str]], base_dir: str) -> list[Violation]:
    """Each committed result.json carries a full versions block and no home path."""
    violations: list[Violation] = []
    for server in sorted(folders):
        if "result.json" not in folders[server]:
            continue  # Already reported by check_file_set.
        path = repo_root / base_dir / server / "result.json"
        raw = path.read_text(encoding="utf-8")

        for marker in _HOME_PATH_MARKERS:
            if marker in raw:
                violations.append(
                    Violation("stamp", f"{server}: result.json contains a home path ({marker!r})")
                )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            violations.append(Violation("stamp", f"{server}: result.json is not valid JSON: {exc}"))
            continue

        versions = data.get("versions")
        if not isinstance(versions, dict):
            violations.append(Violation("stamp", f"{server}: result.json has no versions block"))
            continue
        for field in _REQUIRED_VERSION_FIELDS:
            if not versions.get(field):
                violations.append(
                    Violation("stamp", f"{server}: versions.{field} is missing or empty")
                )
    return violations


def check_manifest(manifest_path: Path) -> list[Violation]:
    """servers.toml still parses through the loader the harness uses."""
    from eval_harness.manifest import load_manifest

    if not manifest_path.exists():
        return [Violation("manifest", f"{manifest_path} does not exist")]
    try:
        load_manifest(manifest_path)
    except Exception as exc:  # noqa: BLE001 — any parse failure is the finding
        return [Violation("manifest", f"{manifest_path} does not parse: {exc}")]
    return []


def check_report_current(repo_root: Path, base_dir: str, report_path: Path) -> list[Violation]:
    """The committed report still contains everything `eval-kit report` generates.

    Compared as a subsequence rather than an equality: the committed report is
    generated with `--with-narrative`, and narrative fragments are local-only, so
    a bare clone can regenerate the skeleton but never the prose. Splicing is
    purely additive, so every generated line must still appear, in order, in the
    committed file. This catches a result.json whose verdict changed without the
    report being regenerated, and stays silent about the narrative it cannot see.
    """
    from eval_harness.report import generate_report

    if not report_path.exists():
        return [Violation("report", f"{report_path} does not exist")]

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "EVAL_REPORT.md"
        generate_report(base_dir=repo_root / base_dir, out_path=fresh, with_narrative=False)
        generated = _significant_lines(fresh)

    committed = iter(_significant_lines(report_path))
    missing = [line for line in generated if not any(c == line for c in committed)]
    if missing:
        sample = "; ".join(line.strip()[:60] for line in missing[:3])
        return [
            Violation(
                "report",
                f"{report_path} is stale — {len(missing)} generated line(s) absent, "
                f"e.g. {sample!r}. Run `uv run eval-kit report --with-narrative`.",
            )
        ]
    return []


def _significant_lines(path: Path) -> list[str]:
    """Report lines with the self-stamped generation time dropped."""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if _VOLATILE_REPORT_MARKER not in line
    ]


def check_artifacts(
    repo_root: Path,
    base_dir: str = "eval",
    manifest: str = "servers/servers.toml",
    report: str = "doc/EVAL_REPORT.md",
) -> list[Violation]:
    """Run every integrity check, returning all violations found."""
    folders = tracked_eval_files(repo_root, base_dir)
    violations = check_file_set(folders)
    violations += check_stamps(repo_root, folders, base_dir)
    violations += check_manifest(repo_root / manifest)
    violations += check_report_current(repo_root, base_dir, repo_root / report)
    return violations
