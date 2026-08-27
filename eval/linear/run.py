"""
Smoke-test runner for generated linear/ wrappers.
Transport: Streamable HTTP  (https://mcp.linear.app/mcp)
Auth: OAuth (browser flow via mcpgen)

Args come from eval/linear/linear.verify.json (real, pre-scrub probe args).
No tool in linear.shapes.json carries a discriminator, so every tool is
called exactly once.

Usage:
    # First time: authenticate
    mcpgen login linear

    # Then run:
    python eval/linear/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linear

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.linear.app/mcp"
SERVER_NAME = "linear"

# Real ids taken from linear.verify.json (gitignored, pre-scrub probe args).
TEAM_ID = "fa7c8348-bc17-4c25-a275-667411a0476b"
STATUS_ID = "f3385697-bee9-452d-a327-e86dbf2103ff"
ISSUE_ID = "SVI-1"


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
        # Skipped mutating tools: create_attachment, create_attachment_from_upload,
        # create_issue_label, delete_attachment, delete_comment, delete_diff_comment,
        # delete_status_update, merge_diff, prepare_attachment_upload,
        # resolve_diff_thread, save_comment, save_diff_comment, save_document,
        # save_issue, save_milestone, save_project, save_release, save_release_note,
        # save_status_update, share_issue, submit_diff_review, unshare_issue.
        # Skipped read-only tools with no probed args (they need a real id that
        # linear.verify.json does not carry): extract_images, get_agent_skill,
        # get_attachment, get_diff, get_diff_threads, get_document, get_milestone,
        # get_project, get_release, get_release_note, list_milestones.

        # get_workspace -> Workspace
        workspace = await linear.get_workspace(caller)
        print(f"get_workspace: id={workspace.get('id')!r}  name={workspace.get('name')!r}")

        # get_user -> User
        me = await linear.get_user(caller, query="me")
        print(f"get_user: id={me.get('id')!r}  displayName={me.get('displayName')!r}")

        # list_users -> list[UserSummary]
        users = await linear.list_users(caller, limit=10)
        print(f"list_users: {len(users)} item(s)")

        # list_teams -> list[TeamSummary]
        teams = await linear.list_teams(caller, limit=5)
        print(f"list_teams: {len(teams)} item(s)")

        # get_team -> Team
        team = await linear.get_team(caller, query=TEAM_ID)
        print(f"get_team: id={team.get('id')!r}  name={team.get('name')!r}")

        # list_issue_statuses -> list[IssueStatusSummary]
        statuses = await linear.list_issue_statuses(caller, team=TEAM_ID)
        print(f"list_issue_statuses: {len(statuses)} item(s)")

        # get_issue_status -> IssueStatus
        status = await linear.get_issue_status(
            caller, id=STATUS_ID, name="In Review", team=TEAM_ID
        )
        print(f"get_issue_status: id={status.get('id')!r}  name={status.get('name')!r}")

        # list_issue_labels -> list[IssueLabelSummary]
        issue_labels = await linear.list_issue_labels(caller, limit=10)
        print(f"list_issue_labels: {len(issue_labels)} item(s)")

        # list_issues -> list[IssueSummary]
        issues = await linear.list_issues(caller, limit=5)
        print(f"list_issues: {len(issues)} item(s)")

        # get_issue -> Issue
        issue = await linear.get_issue(caller, id=ISSUE_ID)
        print(f"get_issue: id={issue.get('id')!r}  title={issue.get('title')!r}")

        # list_comments -> list (untyped records)
        comments = await linear.list_comments(caller, issueId=ISSUE_ID, limit=5)
        print(f"list_comments: {len(comments)} item(s)")

        # list_projects -> list (untyped records)
        projects = await linear.list_projects(caller, limit=5)
        print(f"list_projects: {len(projects)} item(s)")

        # list_project_labels -> list (untyped records)
        project_labels = await linear.list_project_labels(caller, limit=10)
        print(f"list_project_labels: {len(project_labels)} item(s)")

        # list_documents -> list (untyped records)
        documents = await linear.list_documents(caller, limit=5)
        print(f"list_documents: {len(documents)} item(s)")

        # list_cycles -> list (untyped records)
        cycles = await linear.list_cycles(caller, teamId=TEAM_ID)
        print(f"list_cycles: {len(cycles)} item(s)")

        # list_diffs -> list (untyped records)
        diffs = await linear.list_diffs(caller, limit=5)
        print(f"list_diffs: {len(diffs)} item(s)")

        # list_releases -> list (untyped records)
        releases = await linear.list_releases(caller, limit=5)
        print(f"list_releases: {len(releases)} item(s)")

        # list_release_notes -> list (untyped records)
        release_notes = await linear.list_release_notes(caller, limit=5)
        print(f"list_release_notes: {len(release_notes)} item(s)")

        # list_release_pipelines -> list (untyped records)
        pipelines = await linear.list_release_pipelines(caller, limit=5)
        print(f"list_release_pipelines: {len(pipelines)} item(s)")

        # get_status_updates -> list (untyped records)
        status_updates = await linear.get_status_updates(caller, type="project", limit=5)
        print(f"get_status_updates: {len(status_updates)} item(s)")

        # list_agent_skills -> list (untyped records)
        agent_skills = await linear.list_agent_skills(caller)
        print(f"list_agent_skills: {len(agent_skills)} item(s)")

        # search_documentation -> list[DocumentationHit]
        docs = await linear.search_documentation(caller, query="cycles")
        print(f"search_documentation: {len(docs)} item(s)")


if __name__ == "__main__":
    asyncio.run(main())
