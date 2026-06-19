"""
Smoke-test runner for generated deepwiki/ wrappers.
Transport: HTTP/SSE  (https://mcp.deepwiki.com/mcp)
Auth: none (public endpoint)

Usage:
    python eval/deepwiki/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import deepwiki

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.deepwiki.com/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # Skipped mutating tools: (none — all tools are read-only)

    # read_wiki_structure -> Any
    # Get a list of documentation topics for a GitHub repository.
    structure = await deepwiki.read_wiki_structure(caller, repoName="microsoft/vscode")
    print(f"read_wiki_structure: {type(structure).__name__}")

    # read_wiki_contents -> Any
    # View documentation about a GitHub repository.
    contents = await deepwiki.read_wiki_contents(caller, repoName="microsoft/vscode")
    print(f"read_wiki_contents: {type(contents).__name__}")

    # ask_question -> Any
    # Ask any question about a GitHub repository and get an AI-powered response.
    answer = await deepwiki.ask_question(
        caller,
        repoName="microsoft/vscode",
        question="What is the main architecture?",
    )
    print(f"ask_question: {type(answer).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
