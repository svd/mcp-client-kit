"""
Smoke-test runner for generated huggingface/ wrappers.
Transport: Streamable HTTP  (https://huggingface.co/mcp)
Auth: none (public endpoint)

Args come from huggingface.verify.json (real, pre-scrub probe args).
No tool in huggingface.shapes.json declares a `discriminator`, so there are no
discriminated variants to fan out over. Where a tool was probed with more than
one arg set (hf_fs, hub_repo_details), each probed arg set is called once so the
runner covers every payload the probe actually established.

Every tool is annotated `-> Any` and returns a flat string payload
(shapes.json `_observed_shape: "str"`), so prints report type + size + a head
slice rather than drilling into fields.

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
    head = value[:120].replace("\n", " ") if isinstance(value, str) else value
    print(f"{label}: {type(value).__name__} len={size} head={head!r}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — every tool exposed by this server is
        # read-only. hf_fs can express writes through its `operations` payload,
        # but only the read commands (ls, stat) probed here are called.

        # hf_whoami -> Any   (identity first; no args, probed_args == {})
        me = await huggingface.hf_whoami(caller)
        _show("hf_whoami", me)

        # hf_fs -> Any   (probe 1: ls a virtual hub path)
        fs_ls = await huggingface.hf_fs(
            caller,
            operations=[{"cmd": "ls", "args": ["hf://models/trending", "--limit", "5"]}],
        )
        _show("hf_fs(ls trending)", fs_ls)

        # hf_fs -> Any   (probe 2: stat a single repo path)
        fs_stat = await huggingface.hf_fs(
            caller,
            operations=[{"cmd": "stat", "args": ["hf://models/openai/gpt-oss-120b"]}],
        )
        _show("hf_fs(stat gpt-oss-120b)", fs_stat)

        # hub_repo_search -> Any   (main search call)
        search = await huggingface.hub_repo_search(
            caller,
            query="bert",
            repo_types=["model"],
            limit=3,
        )
        _show("hub_repo_search(bert)", search)

        # hub_repo_details -> Any   (probe 1: model repo, default operations)
        model_details = await huggingface.hub_repo_details(
            caller,
            repo_ids=["openai/gpt-oss-120b"],
            repo_type="model",
        )
        _show("hub_repo_details(model)", model_details)

        # hub_repo_details -> Any   (probe 2: dataset repo, explicit operations)
        dataset_details = await huggingface.hub_repo_details(
            caller,
            repo_ids=["stanfordnlp/imdb"],
            repo_type="dataset",
            operations=["overview", "dataset_structure"],
        )
        _show("hub_repo_details(dataset)", dataset_details)


if __name__ == "__main__":
    asyncio.run(main())
