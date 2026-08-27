"""
Smoke-test runner for generated deepwiki/ wrappers.
Transport: Streamable HTTP  (https://mcp.deepwiki.com/mcp)
Auth: none (public endpoint)

Args come from eval/deepwiki/deepwiki.verify.json (real, pre-scrub probe args).

Usage:
    python eval/deepwiki/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file, so its own directory goes on the
# path ahead of the package-style parent entry from the skeleton.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))
import deepwiki

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.deepwiki.com/mcp"

# Real probe arg shared by all three tools (deepwiki.verify.json).
REPO = "facebook/react"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — every deepwiki tool is read-only.
        # No discriminated tools: shapes.json records no discriminator/variants,
        # so each tool gets exactly one call.

        # read_wiki_structure -> Any  (observed shape: str, ~1.4 KB)
        # Get a list of documentation topics for a GitHub repository.
        structure = await deepwiki.read_wiki_structure(caller, repoName=REPO)
        print(f"read_wiki_structure: {type(structure).__name__} len={len(structure)}")

        # read_wiki_contents -> Any  (observed shape: str, ~621 KB)
        # View documentation about a GitHub repository.
        contents = await deepwiki.read_wiki_contents(caller, repoName=REPO)
        print(f"read_wiki_contents: {type(contents).__name__} len={len(contents)}")

        # ask_question -> Any  (observed shape: str, ~2.8 KB)
        # Ask any question about a GitHub repository, answered against its wiki.
        answer = await deepwiki.ask_question(
            caller,
            repoName=REPO,
            question="What is the fiber reconciler?",
        )
        print(f"ask_question: {type(answer).__name__} len={len(answer)}")


if __name__ == "__main__":
    asyncio.run(main())
