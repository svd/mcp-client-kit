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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import huggingface

from mcpgen import McpBridgeCaller

SERVER_URL = "https://huggingface.co/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — every tool this server exposes is read-only.
        # All args below come from eval/huggingface/huggingface.verify.json (real,
        # pre-scrub probe args) plus the extra probed variants recorded in the
        # *.probe-raw.json sidecars. Every tool returns prose (-> Any), so each
        # print reports the response type and length rather than drilling fields.

        # hf_whoami -> Any  (auth context; unauthenticated on a public endpoint)
        me = await huggingface.hf_whoami(caller)
        print(f"hf_whoami: {type(me).__name__} ({len(str(me))} chars)")

        # hf_fs -> Any  (variant: ls — trending models listing)
        fs_ls = await huggingface.hf_fs(
            caller,
            operations=[
                {"cmd": "ls", "args": ["hf://models/trending", "--limit", "5"]}
            ],
        )
        print(f"hf_fs(ls): {type(fs_ls).__name__} ({len(str(fs_ls))} chars)")

        # hf_fs -> Any  (variant: stat — filesystem metadata for one repo)
        fs_stat = await huggingface.hf_fs(
            caller,
            operations=[
                {"cmd": "stat", "args": ["hf://models/google-bert/bert-base-uncased"]}
            ],
        )
        print(f"hf_fs(stat): {type(fs_stat).__name__} ({len(str(fs_stat))} chars)")

        # hub_repo_search -> Any  (discovery before detail lookups)
        search = await huggingface.hub_repo_search(
            caller,
            query="bert",
            repo_types=["model"],
            limit=3,
        )
        print(f"hub_repo_search: {type(search).__name__} ({len(str(search))} chars)")

        # hub_repo_details -> Any  (variant: repo_type="model")
        details_model = await huggingface.hub_repo_details(
            caller,
            repo_ids=["google-bert/bert-base-uncased"],
            repo_type="model",
            operations=["overview"],
        )
        print(
            f"hub_repo_details(model): {type(details_model).__name__} "
            f"({len(str(details_model))} chars)"
        )

        # hub_repo_details -> Any  (variant: repo_type="dataset")
        details_dataset = await huggingface.hub_repo_details(
            caller,
            repo_ids=["rajpurkar/squad"],
            repo_type="dataset",
            operations=["overview"],
        )
        print(
            f"hub_repo_details(dataset): {type(details_dataset).__name__} "
            f"({len(str(details_dataset))} chars)"
        )


if __name__ == "__main__":
    asyncio.run(main())
