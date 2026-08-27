"""
Smoke-test runner for generated huggingface/ wrappers.
Transport: Streamable HTTP  (https://huggingface.co/mcp)
Auth: none (public endpoint)

Args come from huggingface.verify.json (real, pre-scrub probe args).
No tool in huggingface.shapes.json declares a discriminator, so each tool is
called exactly once. Every tool returns a flat string payload
(shapes.json `_observed_shape: "str"`, wrapper annotation `-> Any`).

Usage:
    python eval/huggingface/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file (eval/huggingface/huggingface.py),
# so the artifact dir itself is what has to be importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import huggingface

from mcpgen import McpBridgeCaller

SERVER_URL = "https://huggingface.co/mcp"


def _show(label: str, value: object) -> None:
    """Shape-aware print for a `-> Any` tool that returns a string payload."""
    size = len(value) if hasattr(value, "__len__") else "n/a"
    print(f"{label}: {type(value).__name__} len={size}")
    if isinstance(value, str) and value:
        print(f"  first line: {value.splitlines()[0][:100]!r}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — the server exposes only read-only tools)
        # Args below are the real pre-scrub probe args from huggingface.verify.json.

        # 1. Identity — hf_whoami -> Any  (observed: str, ~321 B)
        # No parameters; probed_args in shapes.json is {} — nothing to supply.
        me = await huggingface.hf_whoami(caller)
        _show("hf_whoami", me)

        # 2. Discovery — hub_repo_search -> Any  (observed: str, ~1.5 KB)
        search = await huggingface.hub_repo_search(
            caller, query="bert", repo_types=["model"], limit=3
        )
        _show("hub_repo_search", search)

        # 3. Detail lookup for a known repo — hub_repo_details -> Any
        #    (observed: str, ~2.0 KB)
        details = await huggingface.hub_repo_details(
            caller, repo_ids=["openai/gpt-oss-120b"], repo_type="model"
        )
        _show("hub_repo_details", details)

        # 4. Filesystem view — hf_fs -> Any  (observed: str, ~1.0 KB)
        listing = await huggingface.hf_fs(
            caller,
            operations=[
                {"cmd": "ls", "args": ["hf://models/trending", "--limit", "5"]}
            ],
        )
        _show("hf_fs", listing)


if __name__ == "__main__":
    asyncio.run(main())
