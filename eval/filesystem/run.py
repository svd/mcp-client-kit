"""
Smoke-test runner for generated filesystem/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-filesystem /private/tmp)
Auth: none

Every tool on this server returns prose or a JSON-encoded blob rather than a
shaped record, so all wrappers are annotated `-> Any` and there are no
discriminated return models. `read_text_file` was probed twice (full read and
`head=5`), so it is called once per probed argument variant.

Args below come from eval/filesystem/filesystem.verify.json (real, pre-scrub
values). The directory paths point at a scratchpad directory from the probe
session; if it no longer exists, set PROBE_DIR to any directory under
/private/tmp before running.

Usage:
    python eval/filesystem/run.py
"""
import asyncio
import importlib.util
import os
import sys

# The wrapper module file is "filesystem.py", but its parent directory is also
# named "filesystem/", so a plain `import filesystem` would resolve to that
# directory as a namespace package instead of the wrappers. Load it by path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "filesystem_wrappers",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "filesystem.py"),
)
filesystem_wrappers = importlib.util.module_from_spec(_spec)
sys.modules["filesystem_wrappers"] = filesystem_wrappers
_spec.loader.exec_module(filesystem_wrappers)

from mcpgen import McpBridgeCaller

# Real probed args (eval/filesystem/filesystem.verify.json).
PROBE_DIR = (
    "/private/tmp/claude-501/-Users-Sviataslau-Svirydau-src-mcp-client-kit-eval"
    "/fdfef571-2efc-4233-8a2f-502e07c83bd1/scratchpad"
)
PROBE_FILE = "/private/tmp/c2.txt"
PROBE_FILE_2 = "/private/tmp/cli_flags.txt"


def _preview(value: object, limit: int = 120) -> str:
    """First line of a response, truncated — these tools return str/Any."""
    text = value if isinstance(value, str) else repr(value)
    first = text.splitlines()[0] if text.splitlines() else ""
    return first[:limit] + ("..." if len(first) > limit else "")


async def main() -> None:
    caller = McpBridgeCaller(
        cmd="npx -y @modelcontextprotocol/server-filesystem /private/tmp"
    )

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: create_directory, edit_file, move_file, write_file
        # Skipped read-only tool: read_media_file (never probed; returns binary media)

        # list_allowed_directories -> Any
        roots = await filesystem_wrappers.list_allowed_directories(caller)
        print(f"list_allowed_directories: {type(roots).__name__}  {_preview(roots)}")

        # get_file_info -> Any
        info = await filesystem_wrappers.get_file_info(caller, path=PROBE_FILE)
        print(f"get_file_info: {type(info).__name__}  {_preview(info)}")

        # list_directory -> Any
        listing = await filesystem_wrappers.list_directory(caller, path=PROBE_DIR)
        print(f"list_directory: {type(listing).__name__}  {_preview(listing)}")

        # list_directory_with_sizes -> Any
        sized = await filesystem_wrappers.list_directory_with_sizes(
            caller, path=PROBE_DIR, sortBy="name"
        )
        print(f"list_directory_with_sizes: {type(sized).__name__}  {_preview(sized)}")

        # directory_tree -> Any  (payload is a JSON-encoded list of entries)
        tree = await filesystem_wrappers.directory_tree(caller, path=PROBE_DIR)
        print(f"directory_tree: {type(tree).__name__}  {_preview(tree)}")

        # search_files -> Any
        matches = await filesystem_wrappers.search_files(
            caller, path=PROBE_DIR, pattern="*.json"
        )
        print(f"search_files: {type(matches).__name__}  {_preview(matches)}")

        # read_text_file -> Any  (probed variant 1: whole file)
        text_full = await filesystem_wrappers.read_text_file(caller, path=PROBE_FILE)
        print(f"read_text_file(full): {type(text_full).__name__}  {_preview(text_full)}")

        # read_text_file -> Any  (probed variant 2: head=5)
        text_head = await filesystem_wrappers.read_text_file(
            caller, path=PROBE_FILE_2, head=5
        )
        print(f"read_text_file(head=5): {type(text_head).__name__}  {_preview(text_head)}")

        # read_file -> Any
        contents = await filesystem_wrappers.read_file(caller, path=PROBE_FILE)
        print(f"read_file: {type(contents).__name__}  {_preview(contents)}")

        # read_multiple_files -> Any
        multi = await filesystem_wrappers.read_multiple_files(
            caller, paths=[PROBE_FILE, PROBE_FILE_2]
        )
        print(f"read_multiple_files: {type(multi).__name__}  {_preview(multi)}")


if __name__ == "__main__":
    asyncio.run(main())
