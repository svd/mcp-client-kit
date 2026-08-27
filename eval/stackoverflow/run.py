"""
Smoke-test runner for generated stackoverflow/ wrappers.
Transport: Streamable HTTP  (https://mcp.stackoverflow.com)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login stackoverflow

    # Then run:
    python stackoverflow/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import stackoverflow

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.stackoverflow.com"
SERVER_NAME = "stackoverflow"


async def main() -> None:
    # Ensure a valid OAuth token is available (silent refresh or browser prompt).
    # LoginWontHelp covers both failures the browser cannot fix: the token was
    # issued but the check after it failed, or the token endpoint was unreachable
    # so the cached grant could not be renewed. Stop rather than sending the user
    # back to the browser.
    try:
        await ensure_login(SERVER_NAME)
    except LoginWontHelp as exc:
        print(f"[{SERVER_NAME}] {exc}", file=sys.stderr)
        sys.exit(1)
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: one initialize() and one OAuth
    # pre-flight refresh, instead of one per tool call.
    async with caller.connected():
        # Skipped mutating tools: none — both tools on this server are read-only.
        # Args are the real probed values from stackoverflow.verify.json.
        # Neither tool is discriminated (no `discriminator`/`variants` in
        # stackoverflow.shapes.json), so one call per tool.

        # so_search -> list[SearchQuestionItem]  (lexical search; discovery step)
        hits = await stackoverflow.so_search(
            caller, query="python asyncio gather exception handling"
        )
        print(f"so_search: {len(hits)} question(s)")
        if hits:
            top = hits[0]
            print(
                f"  top: question_id={top.get('question_id')!r} "
                f"score={top.get('score')!r} title={top.get('title')!r}"
            )

        # get_content -> list[ContentItem]  (batch fetch: question + answer ids)
        content = await stackoverflow.get_content(
            caller, query="SO_Q54987361 SO_A54987732"
        )
        print(f"get_content: {len(content)} item(s)")
        for entry in content:
            print(
                f"  - Type={entry.get('Type')!r} Id={entry.get('Id')!r} "
                f"Site={entry.get('Site')!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
