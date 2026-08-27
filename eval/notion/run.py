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
        # Skipped mutating tools: notion_convert_page_to_skill, notion_create_attachment,
        # notion_create_comment, notion_create_database, notion_create_file_upload,
        # notion_create_folder, notion_create_pages, notion_create_view,
        # notion_duplicate_page, notion_move_pages, notion_update_data_source,
        # notion_update_folder, notion_update_page, notion_update_view.
        # Also skipped (read-only, but need a server-side ephemeral id that no probe
        # produced): notion_get_async_task, notion_download_attachment.
        #
        # Args come from notion.verify.json (real, pre-scrub) unless noted otherwise.

        # notion-get-users -> list[UserSummary]
        users = await notion.notion_get_users(caller)
        print(f"notion-get-users: {len(users)} item(s)")

        # notion-get-teams -> TeamsResult
        teams = await notion.notion_get_teams(caller)
        print(
            f"notion-get-teams: joined={len(teams.get('joinedTeams') or [])} "
            f"other={len(teams.get('otherTeams') or [])} hasMore={teams.get('hasMore')}"
        )

        # notion-list-recent-pages -> list[RecentPageSummary]
        recent = await notion.notion_list_recent_pages(caller, limit=5)
        print(f"notion-list-recent-pages: {len(recent)} item(s)")

        # notion-list-favorite-pages -> list[SidebarPageSummary]
        favorites = await notion.notion_list_favorite_pages(caller, limit=5)
        print(f"notion-list-favorite-pages: {len(favorites)} item(s)")

        # notion-list-private-pages -> list[SidebarPageSummary]
        private = await notion.notion_list_private_pages(caller, limit=5)
        print(f"notion-list-private-pages: {len(private)} item(s)")

        # notion-list-shared-pages -> Any
        # Unwrap-only: `results` was empty at probe time, so the element shape is
        # unknown and the wrapper still returns Any.
        shared = await notion.notion_list_shared_pages(caller, limit=5)
        print(f"notion-list-shared-pages: {type(shared).__name__}")

        # notion-fetch -> NotionEntity
        entity = await notion.notion_fetch(caller, id="0c54738b63e44b4abbdf86106f4d37a8")
        print(
            f"notion-fetch: title={entity.get('title')!r} url={entity.get('url')!r} "
            f"text_len={len(entity.get('text') or '')}"
        )

        # notion-get-comments -> Any
        # The probed page carried no discussions, so `{}` is the expected response.
        comments = await notion.notion_get_comments(
            caller, page_id="a4eb85e3403c4d8597acf3749a0ddb1f"
        )
        print(f"notion-get-comments: {type(comments).__name__}")

        # notion-search -> list[SearchContentItem]  (query_type="internal")
        # Args from notion.shapes.json.probed_args: verify.json only carries the
        # "user" variant, and these values contain no PII.
        search_internal = await notion.notion_search(
            caller,
            query="task list",
            query_type="internal",
            content_search_mode="workspace_search",
        )
        print(f"notion-search(internal): {len(search_internal)} record(s)")

        # notion-search -> list[SearchUserItem]  (query_type="user")
        search_user = await notion.notion_search(caller, query="s", query_type="user")
        print(f"notion-search(user): {len(search_user)} record(s)")

        # notion-query-data-sources -> DataSourceQueryResult
        # mode=view was never probed (no view URL reachable without a mutating tool).
        ds = await notion.notion_query_data_sources(
            caller,
            data={
                "mode": "sql",
                "data_source_urls": [
                    "collection://6d3e0a4f-39a1-4219-a6bc-c68be6b635c8"
                ],
                "query": (
                    'SELECT * FROM "collection://'
                    '6d3e0a4f-39a1-4219-a6bc-c68be6b635c8" LIMIT 5'
                ),
            },
        )
        print(f"notion-query-data-sources: has_more={ds.get('has_more')}")

        # notion-search-agents -> Any
        # Probe was inconclusive: both `scope` values returned an error envelope,
        # so this call may fail rather than return a shaped record.
        agents = await notion.notion_search_agents(caller, scope="workspace", limit=5)
        print(f"notion-search-agents: {type(agents).__name__}")

        # notion-query-meeting-notes -> Any
        # Probe was inconclusive: every response was an error, and no args were
        # established, so this is called with defaults only.
        meeting_notes = await notion.notion_query_meeting_notes(caller)
        print(f"notion-query-meeting-notes: {type(meeting_notes).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
