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

    # Skipped mutating tools: gr1_z_image_turbo_generate

    # hf_whoami -> Any  (identity / auth status)
    whoami = await huggingface.hf_whoami(caller)
    print(f"hf_whoami: {type(whoami).__name__}")

    # hf_doc_search -> Any  (doc structure / discovery)
    doc_search = await huggingface.hf_doc_search(caller, query="")
    print(f"hf_doc_search: {type(doc_search).__name__}")

    # hf_doc_fetch -> Any  (fetch a specific doc page)
    doc = await huggingface.hf_doc_fetch(caller, doc_url="https://huggingface.co/docs/transformers/index")
    print(f"hf_doc_fetch: {type(doc).__name__}")

    # hub_repo_search -> Any  (search repos)
    repo_results = await huggingface.hub_repo_search(caller, query="bert", repo_types=["model"], limit=5)
    print(f"hub_repo_search: {type(repo_results).__name__}")

    # hub_repo_details -> Any  (model overview)
    repo_detail = await huggingface.hub_repo_details(caller, repo_ids=["google-bert/bert-base-uncased"], repo_type="model", operations=["overview"])
    print(f"hub_repo_details: {type(repo_detail).__name__}")

    # paper_search -> Any  (ML paper search)
    papers = await huggingface.paper_search(caller, query="attention is all you need", results_limit=3)
    print(f"paper_search: {type(papers).__name__}")

    # space_search -> Any  (semantic space discovery)
    spaces = await huggingface.space_search(caller, query="machine learning")
    print(f"space_search: {type(spaces).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
