"""
Smoke-test runner for generated git/ wrappers.
Transport: stdio  (uvx mcp-server-git)
Auth: none

Usage:
    python git/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import git

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-git")

    # git_branch → Any
    result = await git.git_branch(caller, repo_path='<example-repo-path>', branch_type='all')
    print(f"git_branch: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
