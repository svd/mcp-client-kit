"""
Smoke-test runner for generated notion/ wrappers.
Transport: Streamable HTTP  (https://mcp.notion.com/mcp)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login notion

    # Then run:
    python notion/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import notion

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.notion.com/mcp"
SERVER_NAME = "notion"

# Real args below come from notion/notion.verify.json (unscrubbed probe args).
PAGE_ID = "a4eb85e3403c4d8597acf3749a0ddb1f"
DATABASE_ID = "0c54738b63e44b4abbdf86106f4d37a8"
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
        # Skipped mutating tools: notion_create_attachment, notion_create_comment,
        # notion_create_database, notion_create_file_upload, notion_create_folder,
        # notion_create_pages, notion_create_view, notion_update_data_source,
        # notion_update_folder, notion_update_page, notion_update_view,
        # notion_duplicate_page, notion_move_pages, notion_convert_page_to_skill.
        # Also skipped (read-only but need a live server-side id that no probe
        # recorded): notion_get_async_task, notion_download_attachment.

        # notion-fetch -> NotionEntity  (id='self' identity envelope)
        me = await notion.notion_fetch(caller, id="self")
        print(f"notion-fetch(self): title={me.get('title')!r}  url={me.get('url')!r}")

        # notion-get-teams -> TeamsResult
        teams = await notion.notion_get_teams(caller)
        print(
            f"notion-get-teams: joined={len(teams.get('joinedTeams') or [])} "
            f"other={len(teams.get('otherTeams') or [])} hasMore={teams.get('hasMore')}"
        )

        # notion-get-users -> list[WorkspaceUser]
        users = await notion.notion_get_users(caller, page_size=5)
        print(f"notion-get-users: {len(users)} user(s)")

        # notion-list-recent-pages -> list[RecentPageSummary]
        recent = await notion.notion_list_recent_pages(caller, limit=8)
        print(f"notion-list-recent-pages: {len(recent)} page(s)")

        # notion-list-favorite-pages -> list[SidebarPageSummary]
        favorites = await notion.notion_list_favorite_pages(caller, limit=8)
        print(f"notion-list-favorite-pages: {len(favorites)} page(s)")

        # notion-list-private-pages -> list[SidebarPageSummary]
        private = await notion.notion_list_private_pages(caller, limit=8)
        print(f"notion-list-private-pages: {len(private)} page(s)")

        # notion-list-shared-pages -> Any  (envelope left untyped: probe saw results=[])
        shared = await notion.notion_list_shared_pages(caller, limit=8)
        print(f"notion-list-shared-pages: {type(shared).__name__}")

        # notion-fetch -> NotionEntity  (page id)
        page = await notion.notion_fetch(caller, id=PAGE_ID)
        print(f"notion-fetch(page): title={page.get('title')!r}  url={page.get('url')!r}")

        # notion-fetch -> NotionEntity  (database id)
        database = await notion.notion_fetch(caller, id=DATABASE_ID)
        print(
            f"notion-fetch(database): title={database.get('title')!r}  "
            f"url={database.get('url')!r}"
        )

        # notion-get-comments -> Any  (probed page had no discussions; shape unobserved)
        comments = await notion.notion_get_comments(
            caller, page_id=PAGE_ID, include_all_blocks=True, include_resolved=True
        )
        print(f"notion-get-comments: {type(comments).__name__}")

        # notion-query-data-sources -> list[DataSourceRow]  (SQL mode only was probed)
        rows = await notion.notion_query_data_sources(
            caller,
            data={
                "data_source_urls": [DATA_SOURCE_URL],
                "query": f'SELECT * FROM "{DATA_SOURCE_URL}" LIMIT 5',
            },
        )
        print(f"notion-query-data-sources: {len(rows)} row(s)")

        # notion-search -> list[SearchResultItem]  (query_type='internal')
        hits = await notion.notion_search(
            caller, query="project notes", query_type="internal", page_size=5
        )
        print(f"notion-search(internal): {len(hits)} hit(s)")

        # notion-search -> list[UserSearchItem]  (query_type='user')
        people = await notion.notion_search(
            caller, query="project notes", query_type="user", page_size=5
        )
        print(f"notion-search(user): {len(people)} row(s)")

        # notion-search-agents -> Any  (scope='workspace'; probe returned an error envelope)
        agents_ws = await notion.notion_search_agents(caller, scope="workspace", limit=5)
        print(f"notion-search-agents(workspace): {type(agents_ws).__name__}")

        # notion-search-agents -> Any  (scope='favorites'; second enum value of the
        # required discriminator — probed too, also inconclusive)
        agents_fav = await notion.notion_search_agents(caller, scope="favorites", limit=5)
        print(f"notion-search-agents(favorites): {type(agents_fav).__name__}")

        # notion-query-meeting-notes -> Any  (probe inconclusive; no filter recorded)
        notes = await notion.notion_query_meeting_notes(caller)
        print(f"notion-query-meeting-notes: {type(notes).__name__}")

if __name__ == "__main__":
    asyncio.run(main())
