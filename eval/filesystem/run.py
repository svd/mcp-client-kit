"""
Smoke-test runner for generated filesystem/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-filesystem /private/tmp)
Auth: none

Usage:
    python eval/filesystem/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import filesystem

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-filesystem /private/tmp")

    # Skipped mutating tools: create_directory, edit_file, move_file, write_file
    # Skipped: read_media_file (no shapes entry; requires a binary/image file)
    # Note: read_file is deprecated in favour of read_text_file but included for coverage

    # list_allowed_directories -> Any
    allowed = await filesystem.list_allowed_directories(caller)
    print(f"list_allowed_directories: {type(allowed).__name__}")

    # list_directory -> Any
    listing = await filesystem.list_directory(caller, path="/private/tmp")
    print(f"list_directory: {type(listing).__name__}")

    # list_directory_with_sizes -> Any
    listing_sz = await filesystem.list_directory_with_sizes(caller, path="/private/tmp")
    print(f"list_directory_with_sizes: {type(listing_sz).__name__}")

    # directory_tree -> list[DirectoryEntry]
    tree = await filesystem.directory_tree(caller, path="/private/tmp")
    print(f"directory_tree: {len(tree)} item(s)")

    # get_file_info -> Any
    info = await filesystem.get_file_info(caller, path="/private/tmp")
    print(f"get_file_info: {type(info).__name__}")

    # search_files -> Any
    matches = await filesystem.search_files(caller, path="/private/tmp", pattern="*.json")
    print(f"search_files: {type(matches).__name__}")

    # read_text_file -> Any
    text = await filesystem.read_text_file(caller, path="/private/tmp/mcpgen-eval-test.txt")
    print(f"read_text_file: {type(text).__name__}")

    # read_multiple_files -> Any
    multi = await filesystem.read_multiple_files(caller, paths=["/private/tmp/mcpgen-eval-test.txt"])
    print(f"read_multiple_files: {type(multi).__name__}")

    # read_file -> Any  (DEPRECATED: use read_text_file instead)
    content = await filesystem.read_file(caller, path="/private/tmp/mcpgen-eval-test.txt")
    print(f"read_file: {type(content).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
