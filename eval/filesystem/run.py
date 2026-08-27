"""
Smoke-test runner for generated filesystem/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-filesystem /private/tmp)
Auth: none

Usage:
    python filesystem/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import filesystem

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-filesystem /private/tmp")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: create_directory, edit_file, move_file, write_file
        # Skipped read-only tool without probed args: read_media_file (needs a real
        # image/audio file inside the allowed root; none was probed).
        # All args below come from filesystem.verify.json (real, pre-scrub probe args).
        # No tool has a discriminated return shape; read_text_file was probed with two
        # arg variants (full read, head=5), so both are exercised.

        # list_allowed_directories -> Any
        allowed = await filesystem.list_allowed_directories(caller)
        print(f"list_allowed_directories: {type(allowed).__name__}")

        # list_directory -> Any
        listing = await filesystem.list_directory(caller, path="/private/tmp/_base")
        print(f"list_directory: {type(listing).__name__}")

        # list_directory_with_sizes -> Any
        sized = await filesystem.list_directory_with_sizes(
            caller, path="/private/tmp/_base/eval_harness", sortBy="name"
        )
        print(f"list_directory_with_sizes: {type(sized).__name__}")

        # directory_tree -> list[DirectoryNode]
        tree = await filesystem.directory_tree(caller, path="/private/tmp/_base")
        print(f"directory_tree: {len(tree)} node(s)")
        if tree:
            root = tree[0]
            children = root.get("children") or []
            print(
                f"directory_tree[0]: name={root.get('name')!r} "
                f"type={root.get('type')!r} children={len(children)}"
            )

        # search_files -> Any
        matches = await filesystem.search_files(
            caller, path="/private/tmp/_base", pattern="*.py"
        )
        print(f"search_files: {type(matches).__name__}")

        # get_file_info -> Any
        info = await filesystem.get_file_info(
            caller, path="/private/tmp/_base/eval_harness/versions.py"
        )
        print(f"get_file_info: {type(info).__name__}")

        # read_text_file -> Any  (variant 1: whole file)
        text = await filesystem.read_text_file(
            caller, path="/private/tmp/_base/eval_harness/versions.py"
        )
        print(f"read_text_file: {type(text).__name__}")

        # read_text_file -> Any  (variant 2: head=5)
        head = await filesystem.read_text_file(
            caller, path="/private/tmp/_base/eval_harness/manifest.py", head=5
        )
        print(f"read_text_file(head=5): {type(head).__name__}")

        # read_file -> Any
        raw = await filesystem.read_file(
            caller, path="/private/tmp/_base/eval_harness/versions.py"
        )
        print(f"read_file: {type(raw).__name__}")

        # read_multiple_files -> Any
        multi = await filesystem.read_multiple_files(
            caller,
            paths=[
                "/private/tmp/_base/eval_harness/versions.py",
                "/private/tmp/_base/tests/__init__.py",
            ],
        )
        print(f"read_multiple_files: {type(multi).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
