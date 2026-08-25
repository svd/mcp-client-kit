"""
Smoke-test runner for generated huggingface/ wrappers.
Transport: Streamable HTTP  (https://huggingface.co/mcp)
Auth: none (public endpoint)

Usage:
    python eval/huggingface/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import huggingface

from mcpgen import McpBridgeCaller

SERVER_URL = "https://huggingface.co/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # All tools in huggingface.py are read-only; nothing was skipped.
    # (verify.json also has probed args for hf_doc_fetch, hf_doc_search, and
    # space_search, but those tools are not present in the current
    # huggingface.py wrapper module, so they are omitted here.)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # hf_whoami -> Any  (identity / auth status)
        whoami = await huggingface.hf_whoami(caller)
        print(f"hf_whoami: {type(whoami).__name__}")

        # hub_repo_search -> Any  (search repos; verify.json probe: model repo_type)
        repo_results = await huggingface.hub_repo_search(
            caller, query="bert-base-uncased", repo_types=["model"], limit=5
        )
        print(f"hub_repo_search: {type(repo_results).__name__}")

        # hub_repo_details -> Any  (model overview; verify.json probe 1 of 2)
        repo_detail = await huggingface.hub_repo_details(
            caller,
            repo_ids=["bert-base-uncased"],
            repo_type="model",
            operations=["overview"],
        )
        print(f"hub_repo_details: {type(repo_detail).__name__}")

        # hf_fs -> Any  (browse hf:// resources; verify.json probe 1 of 2: ls trending models)
        listing = await huggingface.hf_fs(
            caller,
            operations=[
                {"cmd": "ls", "args": ["hf://models/trending", "--limit", "5"]}
            ],
        )
        print(f"hf_fs: {type(listing).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
