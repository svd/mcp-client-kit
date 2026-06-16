"""
Smoke-test runner for generated github/ wrappers.
Transport: HTTP  (https://api.githubcopilot.com/mcp/)
Auth: Bearer token (set GITHUB_PAT env var)

Usage:
    GITHUB_PAT=<token> python github/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import github

from mcp_client_kit import McpBridgeCaller

SERVER_URL = "https://api.githubcopilot.com/mcp/"


async def main() -> None:
    bearer = os.environ.get("GITHUB_PAT")
    if not bearer:
        sys.exit("GITHUB_PAT not set")

    caller = McpBridgeCaller(url=SERVER_URL, bearer=bearer)

    # get_commit → Any
    result = await github.get_commit(caller, owner='<example-owner>', repo='<example-repo>', sha='HEAD')
    print(f"get_commit: {type(result).__name__}")

    # get_label → Label
    result = await github.get_label(caller, owner='<example-owner>', repo='<example-repo>', name='<example-label>')
    print(f"get_label: {result}")

    # get_latest_release → Release
    result = await github.get_latest_release(caller, owner='<example-owner>', repo='<example-repo>')
    print(f"get_latest_release: {result}")


if __name__ == "__main__":
    asyncio.run(main())
