"""
Smoke-test runner for generated huggingface/ wrappers.
Transport: HTTP/SSE  (https://huggingface.co/mcp)
Auth: none (public endpoint)

Usage:
    python huggingface/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import huggingface

from mcp_client_kit import McpBridgeCaller

SERVER_URL = "https://huggingface.co/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # hf_doc_fetch → Any
    result = await huggingface.hf_doc_fetch(caller, url='https://huggingface.co/docs/transformers/index')
    print(f"hf_doc_fetch: {type(result).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
