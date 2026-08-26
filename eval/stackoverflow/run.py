"""
Smoke-test runner for generated stackoverflow/ wrappers.
Transport: Streamable HTTP  (https://mcp.stackoverflow.com)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login stackoverflow

    # Then run:
    python eval/stackoverflow/run.py

Note: the wrapper module lives at eval/stackoverflow/stackoverflow.py, inside a
directory of the same name. A bare `import stackoverflow` would resolve to the
directory (as a namespace package) rather than the module, so we load the
wrapper directly from its file path under a distinct module name.

Args below are the real, pre-scrub probe args from stackoverflow.verify.json.
Neither tool is discriminated (no `discriminator`/`variants` in shapes.json);
both probe entries are emitted per tool because each exercises a distinct
content kind (question vs. answer) / query domain.
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
_spec = importlib.util.spec_from_file_location(
    "stackoverflow_wrappers",
    os.path.join(os.path.dirname(__file__), "stackoverflow.py"),
)
stackoverflow = importlib.util.module_from_spec(_spec)
sys.modules["stackoverflow_wrappers"] = stackoverflow
_spec.loader.exec_module(stackoverflow)

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
        # Skipped mutating tools: (none — this server exposes only read tools)

        # so_search -> list[SearchQuestionItem]
        search_py = await stackoverflow.so_search(
            caller, query="python asyncio gather exception handling"
        )
        print(f"so_search(python): {len(search_py)} item(s)")
        if search_py:
            top = search_py[0]
            print(
                f"  top: question_id={top.get('question_id')!r} "
                f"score={top.get('score')!r} "
                f"answers={top.get('answer_count')!r} "
                f"title={top.get('title')!r}"
            )

        # so_search -> list[SearchQuestionItem]  (second probed query)
        search_rs = await stackoverflow.so_search(
            caller, query="rust borrow checker lifetime elision"
        )
        print(f"so_search(rust): {len(search_rs)} item(s)")
        if search_rs:
            top = search_rs[0]
            print(
                f"  top: question_id={top.get('question_id')!r} "
                f"is_answered={top.get('is_answered')!r} "
                f"tags={top.get('tags')!r}"
            )

        # get_content -> list[ContentItem]  (multi-request: question + answer)
        combined = await stackoverflow.get_content(
            caller, query="SO_Q54987361, SO_A54987732"
        )
        print(f"get_content(question+answer): {len(combined)} item(s)")
        for item in combined:
            print(
                f"  Site={item.get('Site')!r} Type={item.get('Type')!r} "
                f"Id={item.get('Id')!r} "
                f"OriginalRequest={item.get('OriginalRequest')!r}"
            )

        # get_content -> list[ContentItem]  (second probed query: single question)
        question = await stackoverflow.get_content(caller, query="SO_Q11227809")
        print(f"get_content(question): {len(question)} item(s)")
        for item in question:
            data = item.get("Data") or {}
            print(
                f"  Site={item.get('Site')!r} Type={item.get('Type')!r} "
                f"Id={item.get('Id')!r} "
                f"Data keys={sorted(data)[:5]}"
            )


if __name__ == "__main__":
    asyncio.run(main())
