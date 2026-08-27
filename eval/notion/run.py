"""
Smoke-test runner for generated notion/ wrappers.
Transport: Streamable HTTP  (https://mcp.notion.com/mcp)
Auth: OAuth (browser flow via mcpgen)

Args come from eval/notion/notion.verify.json (real, pre-scrub probe args) where
present; the two notion-search discriminator variants fall back to the scrubbed
notion.shapes.json probed_args, which carry no placeholders.

Usage:
    # First time: authenticate
    mcpgen login notion

    # Then run:
    python eval/notion/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file, so its own directory goes on the
# path ahead of the package-style parent entry from the skeleton.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))
import notion

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.notion.com/mcp"
SERVER_NAME = "notion"

# Real ids lifted from notion.verify.json.
PAGE_ID = "a4eb85e3403c4d8597acf3749a0ddb1f"
COMMENTS_PAGE_ID = "a65740bf573645fab0e38ee41fdefe2b"
DATA_SOURCE_URL = "collection://6d3e0a4f-39a1-4219-a6bc-c68be6b635c8"


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
        # Skipped mutating tools: notion_convert_page_to_skill,
        # notion_create_attachment, notion_create_comment, notion_create_database,
        # notion_create_file_upload, notion_create_folder, notion_create_pages,
        # notion_create_view, notion_download_attachment, notion_duplicate_page,
        # notion_move_pages, notion_update_data_source, notion_update_folder,
        # notion_update_page, notion_update_view.
        # Also skipped: notion_get_async_task — read-only, but it needs a live
        # task_id that no probe produced.

        # notion-get-users -> list[WorkspaceUserSummary]
        users = await notion.notion_get_users(caller, page_size=5)
        print(f"notion-get-users: {len(users)} user(s)")

        # notion-get-teams -> TeamsResult
        teams = await notion.notion_get_teams(caller)
        print(
            "notion-get-teams: "
            f"joined={len(teams.get('joinedTeams') or [])} "
            f"other={len(teams.get('otherTeams') or [])} "
            f"hasMore={teams.get('hasMore')}"
        )

        # notion-list-private-pages -> list[SidebarPageSummary]
        private_pages = await notion.notion_list_private_pages(caller, limit=5)
        print(f"notion-list-private-pages: {len(private_pages)} page(s)")

        # notion-list-favorite-pages -> list[SidebarPageSummary]
        favorite_pages = await notion.notion_list_favorite_pages(caller, limit=5)
        print(f"notion-list-favorite-pages: {len(favorite_pages)} page(s)")

        # notion-list-recent-pages -> list[RecentPageSummary]
        recent_pages = await notion.notion_list_recent_pages(caller, limit=5)
        print(f"notion-list-recent-pages: {len(recent_pages)} page(s)")

        # notion-list-shared-pages -> Any  (no shape established by probing)
        shared_pages = await notion.notion_list_shared_pages(caller, limit=5)
        print(f"notion-list-shared-pages: {type(shared_pages).__name__}")

        # notion-search -> list[SearchContentItem]  (query_type="internal")
        content_hits = await notion.notion_search(
            caller, query="project plan", query_type="internal", page_size=5
        )
        print(f"notion-search(internal): {len(content_hits)} result(s)")

        # notion-search -> list[SearchUserItem]  (query_type="user")
        user_hits = await notion.notion_search(
            caller, query="a", query_type="user", page_size=5
        )
        print(f"notion-search(user): {len(user_hits)} result(s)")

        # notion-search -> list[SearchContentItem]  (internal, scoped to one page)
        page_hits = await notion.notion_search(
            caller,
            query="tasks",
            query_type="internal",
            page_url=f"https://app.notion.com/p/{PAGE_ID}",
            page_size=5,
        )
        print(f"notion-search(internal, page-scoped): {len(page_hits)} result(s)")

        # notion-fetch -> NotionEntity  (a real page)
        page = await notion.notion_fetch(caller, id=PAGE_ID, include_discussions=True)
        print(
            f"notion-fetch(page): title={page.get('title')!r} url={page.get('url')!r}"
        )

        # notion-fetch -> NotionEntity  ("self": the calling user's own entity)
        me = await notion.notion_fetch(caller, id="self")
        print(f"notion-fetch(self): title={me.get('title')!r} url={me.get('url')!r}")

        # notion-get-comments -> Any  (no shape established by probing)
        comments = await notion.notion_get_comments(
            caller,
            page_id=COMMENTS_PAGE_ID,
            include_resolved=True,
            include_all_blocks=True,
        )
        print(f"notion-get-comments: {type(comments).__name__}")

        # notion-query-data-sources -> DataSourceQueryResult
        rows = await notion.notion_query_data_sources(
            caller,
            data={
                "mode": "sql",
                "data_source_urls": [DATA_SOURCE_URL],
                "query": f'SELECT * FROM "{DATA_SOURCE_URL}" LIMIT 3',
            },
        )
        print(
            "notion-query-data-sources: "
            f"{len(rows.get('results') or [])} row(s) has_more={rows.get('has_more')}"
        )

        # notion-search-agents -> Any  (probe was inconclusive; args are real)
        agents = await notion.notion_search_agents(caller, scope="workspace", limit=5)
        print(f"notion-search-agents: {type(agents).__name__}")

        # notion-query-meeting-notes -> Any  (probe was inconclusive; the probe
        # sent no filter, so this repeats the unfiltered call)
        meeting_notes = await notion.notion_query_meeting_notes(caller)
        print(f"notion-query-meeting-notes: {type(meeting_notes).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
