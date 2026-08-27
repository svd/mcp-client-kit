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
        # Skipped mutating tools: notion-convert-page-to-skill, notion-create-attachment,
        # notion-create-comment, notion-create-database, notion-create-file-upload,
        # notion-create-folder, notion-create-pages, notion-create-view,
        # notion-download-attachment, notion-duplicate-page, notion-move-pages,
        # notion-update-data-source, notion-update-folder, notion-update-page,
        # notion-update-view
        # Also skipped (read-only, but no usable args): notion-get-async-task needs a
        # live task_id, and notion-query-meeting-notes was probe-inconclusive — every
        # probe returned an error, so no working arg set was ever established.
        # Args below come from notion.verify.json (real, pre-scrub) unless noted.

        # notion-get-users -> list[UserSummary]
        users = await notion.notion_get_users(caller, page_size=5)
        print(f"notion-get-users: {len(users)} item(s)")

        # notion-get-teams -> TeamsResult
        teams = await notion.notion_get_teams(caller)
        print(
            f"notion-get-teams: joined={len(teams.get('joinedTeams') or [])} "
            f"other={len(teams.get('otherTeams') or [])} "
            f"hasMore={teams.get('hasMore')!r}"
        )

        # notion-list-recent-pages -> list[RecentPageEntry]
        recent = await notion.notion_list_recent_pages(caller, limit=5)
        print(f"notion-list-recent-pages: {len(recent)} item(s)")

        # notion-list-private-pages -> list[SidebarPageEntry]
        private = await notion.notion_list_private_pages(caller, limit=5)
        print(f"notion-list-private-pages: {len(private)} item(s)")

        # notion-list-favorite-pages -> list[SidebarPageEntry]
        favorites = await notion.notion_list_favorite_pages(caller, limit=5)
        print(f"notion-list-favorite-pages: {len(favorites)} item(s)")

        # notion-list-shared-pages -> Any  (element shape unobserved: Shared section empty)
        shared = await notion.notion_list_shared_pages(caller, limit=5)
        print(f"notion-list-shared-pages: {type(shared).__name__}")

        # notion-search -> list[SearchResultItem]  (query_type="internal")
        hits = await notion.notion_search(
            caller, query="task", query_type="internal", page_size=5
        )
        print(f"notion-search(internal): {len(hits)} record(s)")

        # notion-search -> list[SearchPersonItem]  (query_type="user")
        # verify.json holds only the "internal" probe; the shape spec records that
        # "user" was probed live too, so its args are reconstructed here.
        people = await notion.notion_search(
            caller, query="a", query_type="user", page_size=5
        )
        print(f"notion-search(user): {len(people)} record(s)")

        # notion-fetch -> NotionEntity
        entity = await notion.notion_fetch(
            caller, id="a4eb85e3403c4d8597acf3749a0ddb1f"
        )
        print(
            f"notion-fetch: title={entity.get('title')!r} url={entity.get('url')!r}"
        )

        # notion-get-comments -> Any  (populated shape never observed; probes returned {})
        comments = await notion.notion_get_comments(
            caller, page_id="98b6c7e3e85e46e59f05acf7c4a1cf0e"
        )
        print(f"notion-get-comments: {type(comments).__name__}")

        # notion-query-data-sources -> list[DataSourceRow]  (sql mode; view mode unprobed)
        rows = await notion.notion_query_data_sources(
            caller,
            data={
                "mode": "sql",
                "data_source_urls": [
                    "collection://6d3e0a4f-39a1-4219-a6bc-c68be6b635c8"
                ],
                "query": 'SELECT * FROM "collection://6d3e0a4f-39a1-4219-a6bc-c68be6b635c8" LIMIT 3',
            },
        )
        print(f"notion-query-data-sources: {len(rows)} row(s)")

        # notion-search-agents -> Any  (probe-inconclusive: both scope values errored)
        agents = await notion.notion_search_agents(
            caller, scope="workspace", limit=5
        )
        print(f"notion-search-agents: {type(agents).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
