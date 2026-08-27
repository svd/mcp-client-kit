"""
Smoke-test runner for generated git/ wrappers.
Transport: stdio  (uvx mcp-server-git)
Auth: none

Usage:
    python eval/git/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "git.py", but its parent directory is also named
# "git/", so a plain `import git` would resolve to that directory as a
# namespace package (and, where GitPython is installed, to its `git` package)
# instead of the wrappers. Load it by path as `git_wrappers` instead.
_spec = importlib.util.spec_from_file_location(
    "git_wrappers",
    os.path.join(os.path.dirname(__file__), "git.py"),
)
git_wrappers = importlib.util.module_from_spec(_spec)
sys.modules["git_wrappers"] = git_wrappers
_spec.loader.exec_module(git_wrappers)

from mcpgen import McpBridgeCaller

# Real probed args come from git.verify.json, where every tool was probed
# against this eval repo's own checkout. Deriving the path from this file keeps
# the runner portable instead of hard-coding one machine's absolute path.
REPO_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-git")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: git_add, git_checkout, git_commit,
        # git_create_branch, git_reset
        # No tool in git.shapes.json carries a discriminator, so each read-only
        # tool is called exactly once. Every tool returns prose (observed shape
        # "str"), hence the length/first-line prints.

        # git_status -> Any  (observed: str)
        status = await git_wrappers.git_status(caller, repo_path=REPO_PATH)
        print(f"git_status: {type(status).__name__}, {len(str(status))} char(s)")

        # git_branch -> Any  (observed: str)
        branches = await git_wrappers.git_branch(
            caller, repo_path=REPO_PATH, branch_type="all"
        )
        print(f"git_branch(all): {type(branches).__name__}, {len(str(branches))} char(s)")

        # git_log -> Any  (observed: str)
        log = await git_wrappers.git_log(caller, repo_path=REPO_PATH, max_count=5)
        print(f"git_log(max_count=5): {type(log).__name__}, {len(str(log))} char(s)")

        # git_show -> Any  (observed: str)
        show = await git_wrappers.git_show(caller, repo_path=REPO_PATH, revision="HEAD")
        print(f"git_show(HEAD): {str(show).splitlines()[0] if str(show) else '<empty>'!r}")

        # git_diff_unstaged -> Any  (observed: str)
        unstaged = await git_wrappers.git_diff_unstaged(caller, repo_path=REPO_PATH)
        print(f"git_diff_unstaged: {len(str(unstaged))} char(s)")

        # git_diff_staged -> Any  (observed: str)
        staged = await git_wrappers.git_diff_staged(caller, repo_path=REPO_PATH)
        print(f"git_diff_staged: {len(str(staged))} char(s)")

        # git_diff -> Any  (observed: str)
        diff = await git_wrappers.git_diff(caller, repo_path=REPO_PATH, target="HEAD~1")
        print(f"git_diff(target=HEAD~1): {len(str(diff))} char(s)")


if __name__ == "__main__":
    asyncio.run(main())
