"""
Smoke-test runner for generated linear/ wrappers.
Transport: Streamable HTTP  (https://mcp.linear.app/mcp)
Auth: OAuth (browser flow via mcpgen)

Args come from eval/linear/linear.verify.json (real, pre-scrub probe args).
No tool in linear.shapes.json carries a discriminator, so every tool is called
once.

Usage:
    # First time: authenticate
    mcpgen login linear

    # Then run:
    python eval/linear/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file, so its own directory goes on the
# path ahead of the package-style parent entry from the skeleton.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))
import linear

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.linear.app/mcp"
SERVER_NAME = "linear"

# Real values lifted from linear.verify.json.
TEAM_QUERY = "Sviridov"
USER_QUERY = "Sviridov"
TEAM_ID = "fa7c8348-bc17-4c25-a275-667411a0476b"
ISSUE_ID = "SVI-4"
COMMENTS_ISSUE_ID = "SVI-1"
STATUS_ID = "f2b3c01c-bd17-47dc-98a7-c8efeffe46fd"
STATUS_NAME = "Done"


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
        #
        # Also skipped — read-only, but their probe was inconclusive because the
        # only args available are placeholder ids (00000000-...) or names that do
        # not resolve in this workspace, so a live call raises and aborts the run:
        # get_agent_skill, get_attachment, get_diff, get_diff_threads, get_document,
        # get_milestone, get_project, get_release, get_release_note, list_milestones.
        # Supply real ids in linear.verify.json to bring them back.

        # get_workspace -> Workspace
        workspace = await linear.get_workspace(caller)
        print(
            f"get_workspace: id={workspace.get('id')!r}  "
            f"name={workspace.get('name')!r}  url={workspace.get('url')!r}"
        )

        # get_user -> User
        me = await linear.get_user(caller, query=USER_QUERY)
        print(
            f"get_user: id={me.get('id')!r}  name={me.get('name')!r}  "
            f"displayName={me.get('displayName')!r}"
        )

        # list_users -> list[UserSummary]
        users = await linear.list_users(caller, limit=3)
        print(f"list_users: {len(users)} user(s)")

        # list_teams -> list[Team]
        teams = await linear.list_teams(caller, limit=3)
        print(f"list_teams: {len(teams)} team(s)")

        # get_team -> Team
        team = await linear.get_team(caller, query=TEAM_QUERY)
        print(f"get_team: id={team.get('id')!r}  name={team.get('name')!r}")

        # list_issue_statuses -> list[IssueStatus]
        statuses = await linear.list_issue_statuses(caller, team=TEAM_QUERY)
        print(f"list_issue_statuses: {len(statuses)} status(es)")

        # get_issue_status -> IssueStatus
        status = await linear.get_issue_status(
            caller, id=STATUS_ID, name=STATUS_NAME, team=TEAM_QUERY
        )
        print(
            f"get_issue_status: id={status.get('id')!r}  "
            f"name={status.get('name')!r}  type={status.get('type')!r}"
        )

        # list_issue_labels -> list[IssueLabel]
        labels = await linear.list_issue_labels(caller, limit=3)
        print(f"list_issue_labels: {len(labels)} label(s)")

        # list_cycles -> list (unshaped)
        cycles = await linear.list_cycles(caller, teamId=TEAM_ID, type="current")
        print(f"list_cycles: {len(cycles)} cycle(s)")

        # list_issues -> list[IssueSummary]
        issues = await linear.list_issues(caller, limit=3)
        print(f"list_issues: {len(issues)} issue(s)")

        # get_issue -> Issue
        issue = await linear.get_issue(caller, id=ISSUE_ID)
        print(
            f"get_issue: id={issue.get('id')!r}  title={issue.get('title')!r}  "
            f"status={issue.get('status')!r}"
        )

        # list_comments -> list (unwrap: comments)
        comments = await linear.list_comments(caller, issueId=COMMENTS_ISSUE_ID, limit=3)
        print(f"list_comments: {len(comments)} comment(s)")

        # list_projects -> list (unwrap: projects)
        projects = await linear.list_projects(caller, limit=3)
        print(f"list_projects: {len(projects)} project(s)")

        # list_project_labels -> list (unwrap: labels)
        project_labels = await linear.list_project_labels(caller, limit=3)
        print(f"list_project_labels: {len(project_labels)} label(s)")

        # get_status_updates -> list (unwrap: statusUpdates)
        status_updates = await linear.get_status_updates(caller, type="project", limit=3)
        print(f"get_status_updates: {len(status_updates)} update(s)")

        # list_documents -> list (unwrap: documents)
        documents = await linear.list_documents(caller, limit=3)
        print(f"list_documents: {len(documents)} document(s)")

        # list_diffs -> list (unwrap: diffs)
        diffs = await linear.list_diffs(caller, limit=3)
        print(f"list_diffs: {len(diffs)} diff(s)")

        # list_release_pipelines -> list (unwrap: releasePipelines)
        pipelines = await linear.list_release_pipelines(caller, limit=3)
        print(f"list_release_pipelines: {len(pipelines)} pipeline(s)")

        # list_releases -> list (unwrap: releases)
        releases = await linear.list_releases(caller, limit=3)
        print(f"list_releases: {len(releases)} release(s)")

        # list_release_notes -> list (unwrap: releaseNotes)
        release_notes = await linear.list_release_notes(caller, limit=3)
        print(f"list_release_notes: {len(release_notes)} note(s)")

        # list_agent_skills -> list (unwrap: agentSkills)
        agent_skills = await linear.list_agent_skills(caller, limit=3)
        print(f"list_agent_skills: {len(agent_skills)} skill(s)")

        # extract_images -> Any
        images = await linear.extract_images(
            caller, markdown="![alt](https://example.com/a.png)"
        )
        print(f"extract_images: {type(images).__name__}")

        # search_documentation -> list[DocumentationHit]
        hits = await linear.search_documentation(caller, query="webhooks")
        print(f"search_documentation: {len(hits)} hit(s)")


if __name__ == "__main__":
    asyncio.run(main())
