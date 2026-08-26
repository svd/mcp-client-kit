"""Tests for eval_harness.versions — runtime version detection."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval_harness import versions
from eval_harness.versions import (
    engine_version,
    plugin_dir,
    runtime_versions,
    skill_ref,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_marketplaces(tmp_path: Path, entry: dict | None) -> Path:
    """Write a known_marketplaces.json containing an optional mcp-client-kit entry."""
    data: dict = {
        "claude-plugins-official": {
            "source": {"source": "github", "repo": "anthropics/claude-plugins-official"},
            "installLocation": "/somewhere/else",
        }
    }
    if entry is not None:
        data["mcp-client-kit"] = entry
    path = tmp_path / "known_marketplaces.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_tagged_repo(tmp_path: Path, tag: str) -> Path:
    """Create a git repo with one commit and one tag."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
    }
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "tag", tag], cwd=repo, env=env, check=True)
    return repo


# ---------------------------------------------------------------------------
# engine_version
# ---------------------------------------------------------------------------


def test_engine_version_reads_installed_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """engine_version() returns the installed mcp-client-kit version."""
    monkeypatch.setattr(versions.metadata, "version", lambda name: "9.9.9")
    assert engine_version() == "9.9.9"


def test_engine_version_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing distribution yields None rather than raising."""

    def _raise(name: str) -> str:
        raise versions.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(versions.metadata, "version", _raise)
    assert engine_version() is None


# ---------------------------------------------------------------------------
# plugin_dir
# ---------------------------------------------------------------------------


def test_plugin_dir_from_install_location(tmp_path: Path) -> None:
    """The mcp-client-kit marketplace entry resolves via installLocation."""
    target = tmp_path / "kit-v0.7.0"
    target.mkdir()
    path = write_marketplaces(tmp_path, {"installLocation": str(target)})
    assert plugin_dir(marketplaces_path=path) == target


def test_plugin_dir_falls_back_to_source_path(tmp_path: Path) -> None:
    """Without installLocation, source.path is used."""
    target = tmp_path / "kit-src"
    target.mkdir()
    path = write_marketplaces(tmp_path, {"source": {"source": "directory", "path": str(target)}})
    assert plugin_dir(marketplaces_path=path) == target


def test_plugin_dir_entry_absent(tmp_path: Path) -> None:
    """No mcp-client-kit entry → None."""
    path = write_marketplaces(tmp_path, None)
    assert plugin_dir(marketplaces_path=path) is None


def test_plugin_dir_path_does_not_exist(tmp_path: Path) -> None:
    """A registered but missing directory → None."""
    path = write_marketplaces(tmp_path, {"installLocation": str(tmp_path / "gone")})
    assert plugin_dir(marketplaces_path=path) is None


def test_plugin_dir_unreadable_file(tmp_path: Path) -> None:
    """Missing or malformed known_marketplaces.json → None, no exception."""
    assert plugin_dir(marketplaces_path=tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert plugin_dir(marketplaces_path=bad) is None


# ---------------------------------------------------------------------------
# skill_ref
# ---------------------------------------------------------------------------


def test_skill_ref_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """EVAL_SKILL_REF wins over detection."""
    monkeypatch.setenv("EVAL_SKILL_REF", "v1.2.3")
    assert skill_ref(marketplaces_path=tmp_path / "nope.json") == "v1.2.3"


def test_skill_ref_from_git_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tagged plugin checkout reports its tag."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)
    repo = make_tagged_repo(tmp_path, "v0.7.0")
    path = write_marketplaces(tmp_path, {"installLocation": str(repo)})
    assert skill_ref(marketplaces_path=path) == "v0.7.0"


def test_skill_ref_non_git_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A plugin directory that is not a git checkout → None."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    path = write_marketplaces(tmp_path, {"installLocation": str(plain)})
    assert skill_ref(marketplaces_path=path) is None


def test_skill_ref_no_plugin_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No marketplace entry → None."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)
    path = write_marketplaces(tmp_path, None)
    assert skill_ref(marketplaces_path=path) is None


# ---------------------------------------------------------------------------
# runtime_versions
# ---------------------------------------------------------------------------


def test_runtime_versions_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """runtime_versions() reports engine, skill_ref and skill_path."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)
    monkeypatch.setattr(versions.metadata, "version", lambda name: "0.7.0")
    repo = make_tagged_repo(tmp_path, "v0.7.0")
    path = write_marketplaces(tmp_path, {"installLocation": str(repo)})

    result = runtime_versions(marketplaces_path=path)

    assert result == {
        "engine": "0.7.0",
        "skill_ref": "v0.7.0",
        "skill_path": str(repo),
    }


def test_runtime_versions_all_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Undetectable values are None, never missing keys — the report relies on this."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)

    def _raise(name: str) -> str:
        raise versions.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(versions.metadata, "version", _raise)
    result = runtime_versions(marketplaces_path=tmp_path / "nope.json")

    assert result == {"engine": None, "skill_ref": None, "skill_path": None}


def test_runtime_versions_has_no_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """result.json is git-tracked — a timestamp would churn every diff."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)
    result = runtime_versions(marketplaces_path=tmp_path / "nope.json")
    assert set(result) == {"engine", "skill_ref", "skill_path"}


def test_runtime_versions_contracts_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """skill_path collapses the home prefix — result.json must not carry a username."""
    monkeypatch.delenv("EVAL_SKILL_REF", raising=False)
    monkeypatch.setattr(versions.metadata, "version", lambda name: "0.7.0")
    home = tmp_path / "home"
    (home / "src").mkdir(parents=True)
    repo = make_tagged_repo(home / "src", "v0.7.0")
    monkeypatch.setattr(versions.Path, "home", classmethod(lambda cls: home))
    path = write_marketplaces(tmp_path, {"installLocation": str(repo)})

    result = runtime_versions(marketplaces_path=path)

    assert result["skill_path"] == str(Path("~") / repo.relative_to(home))
    assert str(home) not in result["skill_path"]


def test_contract_home_leaves_outside_paths_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout outside home has no ~ prefix to apply — keep it absolute."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(versions.Path, "home", classmethod(lambda cls: home))
    outside = tmp_path / "opt" / "kit"

    assert versions.contract_home(outside) == str(outside)
