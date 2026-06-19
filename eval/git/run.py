"""
Smoke-test runner for generated git/ wrappers.
Transport: stdio  (uvx mcp-server-git)
Auth: none

Usage:
    python eval/git/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import git

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-git")

    # Skipped mutating tools: git_add, git_checkout, git_commit, git_create_branch, git_reset

    # git_status -> Any
    status = await git.git_status(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval")
    print(f"git_status: {type(status).__name__}")

    # git_branch -> Any
    branches = await git.git_branch(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval", branch_type="local")
    print(f"git_branch: {type(branches).__name__}")

    # git_log -> Any
    log = await git.git_log(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval", max_count=5)
    print(f"git_log: {type(log).__name__}")

    # git_diff_unstaged -> Any
    diff_unstaged = await git.git_diff_unstaged(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval")
    print(f"git_diff_unstaged: {type(diff_unstaged).__name__}")

    # git_diff_staged -> Any
    diff_staged = await git.git_diff_staged(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval")
    print(f"git_diff_staged: {type(diff_staged).__name__}")

    # git_diff -> Any
    diff = await git.git_diff(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval", target="HEAD~1")
    print(f"git_diff: {type(diff).__name__}")

    # git_show -> Any
    show = await git.git_show(caller, repo_path="/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval", revision="HEAD")
    print(f"git_show: {type(show).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
