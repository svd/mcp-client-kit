"""Runtime version detection for eval runs.

An eval result is only comparable against another run if you know which
generator produced it. Two independent things move:

  * the **engine** — the ``mcp-client-kit`` distribution installed in this venv,
    which provides the ``mcpgen`` CLI;
  * the **skill** — the ``generate-mcp-wrappers`` SKILL.md, loaded from the
    ``mcp-client-kit`` plugin marketplace directory, whose version is the git
    ref of that checkout.

Every value is best-effort: detection failures return ``None`` rather than
raising, so a missing plugin never fails a verify run.
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ENGINE_DISTRIBUTION = "mcp-client-kit"
MARKETPLACE_NAME = "mcp-client-kit"
SKILL_REF_ENV = "EVAL_SKILL_REF"

DEFAULT_MARKETPLACES_PATH = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"


def engine_version() -> str | None:
    """Version of the installed mcp-client-kit distribution, or None if absent."""
    try:
        return metadata.version(ENGINE_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return None


def plugin_dir(marketplaces_path: Path | None = None) -> Path | None:
    """Directory the mcp-client-kit plugin marketplace is served from.

    Reads Claude Code's ``known_marketplaces.json``, preferring ``installLocation``
    and falling back to ``source.path`` for directory-sourced marketplaces.
    Returns None if the file is unreadable, the entry is absent, or the recorded
    path no longer exists.
    """
    path = marketplaces_path or DEFAULT_MARKETPLACES_PATH
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    entry = data.get(MARKETPLACE_NAME)
    if not isinstance(entry, dict):
        return None

    location = entry.get("installLocation")
    if not location:
        source = entry.get("source")
        if isinstance(source, dict):
            location = source.get("path")
    if not location:
        return None

    candidate = Path(location)
    return candidate if candidate.is_dir() else None


def _git_describe(repo: Path) -> str | None:
    """``git describe --tags --always --dirty`` in repo, or None if that fails."""
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def skill_ref(marketplaces_path: Path | None = None) -> str | None:
    """Git ref of the checkout the generate-mcp-wrappers skill is loaded from.

    ``EVAL_SKILL_REF`` overrides detection — useful when the skill is served
    from somewhere that is not a git checkout.
    """
    override = os.environ.get(SKILL_REF_ENV)
    if override:
        return override

    directory = plugin_dir(marketplaces_path)
    if directory is None:
        return None
    return _git_describe(directory)


def runtime_versions(marketplaces_path: Path | None = None) -> dict[str, str | None]:
    """Engine and skill versions for this run.

    All three keys are always present (None when undetectable) so consumers can
    read them without guarding. Deliberately carries no timestamp: result.json is
    git-tracked, and a clock value would churn every diff.
    """
    directory = plugin_dir(marketplaces_path)
    return {
        "engine": engine_version(),
        "skill_ref": skill_ref(marketplaces_path),
        "skill_path": str(directory) if directory is not None else None,
    }
