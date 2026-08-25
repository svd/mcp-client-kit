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

        # list_allowed_directories -> Any
        allowed = await filesystem.list_allowed_directories(caller)
        print(f"list_allowed_directories: {type(allowed).__name__}")

        # list_directory -> Any
        listing = await filesystem.list_directory(caller, path="/private/tmp")
        print(f"list_directory: {type(listing).__name__}")

        # list_directory_with_sizes -> Any
        listing_sized = await filesystem.list_directory_with_sizes(caller, path="/private/tmp")
        print(f"list_directory_with_sizes: {type(listing_sized).__name__}")

        # directory_tree -> Any
        tree = await filesystem.directory_tree(caller, path="/private/tmp/claude-501")
        print(f"directory_tree: {type(tree).__name__}")

        # search_files -> Any
        found = await filesystem.search_files(caller, path="/private/tmp", pattern="*.py")
        print(f"search_files: {type(found).__name__}")

        # get_file_info -> Any
        info = await filesystem.get_file_info(caller, path="/private/tmp/rem.py")
        print(f"get_file_info: {type(info).__name__}")

        # read_text_file -> Any  (probed variants: plain, head, tail)
        text_full = await filesystem.read_text_file(caller, path="/private/tmp/rem.py")
        print(f"read_text_file(full): {type(text_full).__name__}")

        text_head = await filesystem.read_text_file(caller, path="/private/tmp/rem.py", head=3)
        print(f"read_text_file(head=3): {type(text_head).__name__}")

        text_tail = await filesystem.read_text_file(caller, path="/private/tmp/rem.py", tail=3)
        print(f"read_text_file(tail=3): {type(text_tail).__name__}")

        # read_file -> Any  (deprecated by the server in favor of read_text_file)
        legacy_text = await filesystem.read_file(caller, path="/private/tmp/rem.py")
        print(f"read_file: {type(legacy_text).__name__}")

        # read_multiple_files -> Any
        multi = await filesystem.read_multiple_files(
            caller, paths=["/private/tmp/rem.py", "/private/tmp/serve.out"]
        )
        print(f"read_multiple_files: {type(multi).__name__}")

        # read_media_file -> MediaFile
        # NOTE: probed_args for this tool were PII-scrubbed in shapes.json
        # (probe_args_scrubbed=true); verify.json supplies the real path used
        # during probing, so it is used here instead of the scrubbed placeholder.
        media = await filesystem.read_media_file(
            caller,
            path="/private/tmp/claude-501/-Users-Sviataslau-Svirydau-Library-CloudStorage-OneDrive-EPAM-Projects-PPG-KB/d7e60a78-fbb4-480d-8a80-89042e713385/scratchpad/img004.png",
        )
        print(f"read_media_file: type={media.get('type')!r} mimeType={media.get('mimeType')!r} has_data={media.get('has_data')!r}")


if __name__ == "__main__":
    asyncio.run(main())
