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
        # Skipped mutating tools: none — every tool on this server is readOnlyHint=true.
        # Args below are the real probed values from stackoverflow.verify.json.
        # No tool on this server is discriminated, so each probed arg-set is
        # emitted as its own call to exercise the input variants that were probed.

        # so_search -> list[SearchQuestionItem]  (lexical search; discovery step)
        py_hits = await stackoverflow.so_search(
            caller, query="python asyncio gather exception handling"
        )
        print(f"so_search(python): {len(py_hits)} question(s)")
        if py_hits:
            top = py_hits[0]
            print(
                f"  top: question_id={top.get('question_id')!r} "
                f"score={top.get('score')!r} title={top.get('title')!r}"
            )

        # so_search -> list[SearchQuestionItem]  (second probed query)
        rust_hits = await stackoverflow.so_search(
            caller, query="rust borrow checker lifetime elision"
        )
        print(f"so_search(rust): {len(rust_hits)} question(s)")

        # get_content -> list[ContentItem]  (single question id)
        question = await stackoverflow.get_content(caller, query="SO_Q54987361")
        print(f"get_content(question): {len(question)} item(s)")
        if question:
            item = question[0]
            print(
                f"  item: Id={item.get('Id')!r} Type={item.get('Type')!r} "
                f"Site={item.get('Site')!r}"
            )

        # get_content -> list[ContentItem]  (single answer id)
        answer = await stackoverflow.get_content(caller, query="SO_A54987732")
        print(f"get_content(answer): {len(answer)} item(s)")

        # get_content -> list[ContentItem]  (multi-id batch: question + answer)
        batch = await stackoverflow.get_content(
            caller, query="SO_Q54987361, SO_A75156486"
        )
        print(f"get_content(batch): {len(batch)} item(s)")
        for entry in batch:
            print(f"  - {entry.get('Type')!r} Id={entry.get('Id')!r}")


if __name__ == "__main__":
    asyncio.run(main())
