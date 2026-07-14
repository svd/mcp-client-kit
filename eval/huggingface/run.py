"""
Smoke-test runner for generated huggingface/ wrappers.
Transport: HTTP/SSE  (https://huggingface.co/mcp)
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

    # All discovered tools are read-only; nothing was skipped.

    # hf_whoami -> Any  (identity / auth status)
    whoami = await huggingface.hf_whoami(caller)
    print(f"hf_whoami: {type(whoami).__name__}")

    # hf_doc_search -> Any  (empty query = discovery mode, per tool description)
    doc_search = await huggingface.hf_doc_search(caller, query="")
    print(f"hf_doc_search: {type(doc_search).__name__}")

    # hf_doc_fetch -> Any  (fetch a specific doc page)
    doc = await huggingface.hf_doc_fetch(
        caller, doc_url="https://huggingface.co/docs/accelerate/quicktour"
    )
    print(f"hf_doc_fetch: {type(doc).__name__}")

    # hf_fs -> Any  (browse hf:// resources; ls trending models)
    listing = await huggingface.hf_fs(caller, cmd="ls", args=["hf://models/trending"])
    print(f"hf_fs: {type(listing).__name__}")

    # hub_repo_search -> Any  (search repos)
    repo_results = await huggingface.hub_repo_search(caller, query="llama", limit=5)
    print(f"hub_repo_search: {type(repo_results).__name__}")

    # hub_repo_details -> Any  (model overview)
    # verify.json probed a second variant (repo_type="dataset",
    # operations=["overview", "dataset_structure"]) but hub_repo_details has no
    # discriminator field in shapes.json, so only the first probed entry is used.
    repo_detail = await huggingface.hub_repo_details(
        caller, repo_ids=["bert-base-uncased"], operations=["overview"]
    )
    print(f"hub_repo_details: {type(repo_detail).__name__}")

    # space_search -> Any  (semantic space discovery)
    spaces = await huggingface.space_search(
        caller, query="text to image generation", limit=5
    )
    print(f"space_search: {type(spaces).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
