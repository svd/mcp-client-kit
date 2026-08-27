"""
Smoke-test runner for generated linear/ wrappers.
Transport: Streamable HTTP  (https://mcp.linear.app/mcp)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login linear

    # Then run:
    python linear/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import linear

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.linear.app/mcp"
SERVER_NAME = "linear"


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
        # Args come from linear.verify.json (real, pre-scrub probe args). Tools
        # absent from verify.json have no required params and are called bare.
        # No tool in linear.shapes.json carries a discriminator, so each tool
        # gets exactly one call.

        # --- 1. Identity / workspace ---------------------------------------
        # get_workspace -> Workspace
        workspace = await linear.get_workspace(caller)
        print(f"get_workspace: id={workspace.get('id')!r}  name={workspace.get('name')!r}")

        # get_user -> User
        user = await linear.get_user(caller, query="sviridov")
        print(f"get_user: id={user.get('id')!r}  displayName={user.get('displayName')!r}")

        # list_users -> list[User]  (unwrap: users)
        users = await linear.list_users(caller)
        print(f"list_users: {len(users)} user(s)")

        # --- 2. Teams -------------------------------------------------------
        # get_team -> Team
        team = await linear.get_team(caller, query="Sviridov")
        print(f"get_team: id={team.get('id')!r}  name={team.get('name')!r}")

        # list_teams -> list[Team]  (unwrap: teams)
        teams = await linear.list_teams(caller)
        print(f"list_teams: {len(teams)} team(s)")

        # --- 3. Workflow metadata ------------------------------------------
        # list_issue_statuses -> list[IssueStatus]
        statuses = await linear.list_issue_statuses(caller, team="Sviridov")
        print(f"list_issue_statuses: {len(statuses)} status(es)")

        # get_issue_status -> IssueStatus
        status = await linear.get_issue_status(
            caller,
            id="f3385697-bee9-452d-a327-e86dbf2103ff",
            name="In Review",
            team="Sviridov",
        )
        print(f"get_issue_status: id={status.get('id')!r}  name={status.get('name')!r}")

        # list_issue_labels -> list[IssueLabel]  (unwrap: labels)
        labels = await linear.list_issue_labels(caller)
        print(f"list_issue_labels: {len(labels)} label(s)")

        # list_project_labels -> list  (unwrap: labels)
        project_labels = await linear.list_project_labels(caller)
        print(f"list_project_labels: {len(project_labels)} label(s)")

        # list_cycles -> list  (inner shape unobserved: store empty at probe time)
        cycles = await linear.list_cycles(
            caller, teamId="fa7c8348-bc17-4c25-a275-667411a0476b", type="next"
        )
        print(f"list_cycles: {len(cycles)} cycle(s)")

        # --- 4. Issues ------------------------------------------------------
        # list_issues -> list[IssueSummary]  (unwrap: issues)
        issues = await linear.list_issues(caller, limit=3)
        print(f"list_issues: {len(issues)} issue(s)")

        # get_issue -> Issue
        issue = await linear.get_issue(caller, id="SVI-1")
        print(f"get_issue: id={issue.get('id')!r}  title={issue.get('title')!r}")

        # list_comments -> list  (unwrap: comments)
        comments = await linear.list_comments(caller, issueId="SVI-1")
        print(f"list_comments: {len(comments)} comment(s)")

        # --- 5. Projects / milestones ---------------------------------------
        # list_projects -> list  (unwrap: projects)
        projects = await linear.list_projects(caller)
        print(f"list_projects: {len(projects)} project(s)")

        # get_project -> Any  (probe inconclusive: no success payload observed)
        project = await linear.get_project(caller, query="Sviridov")
        print(f"get_project: {type(project).__name__}")

        # list_milestones -> Any  (probe inconclusive)
        milestones = await linear.list_milestones(caller, project="Sviridov")
        print(f"list_milestones: {type(milestones).__name__}")

        # get_milestone -> Any  (not shaped; args from verify.json)
        milestone = await linear.get_milestone(
            caller, project="Nonexistent Project", query="M1"
        )
        print(f"get_milestone: {type(milestone).__name__}")

        # get_status_updates -> Any
        status_updates = await linear.get_status_updates(caller, type="project", limit=3)
        print(f"get_status_updates: {type(status_updates).__name__}")

        # --- 6. Documents ---------------------------------------------------
        # list_documents -> list  (unwrap: documents)
        documents = await linear.list_documents(caller)
        print(f"list_documents: {len(documents)} document(s)")

        # get_document -> Any  (probe inconclusive; probed with a nonexistent id)
        document = await linear.get_document(caller, id="nonexistent-doc")
        print(f"get_document: {type(document).__name__}")

        # --- 7. Diffs -------------------------------------------------------
        # list_diffs -> list  (unwrap: diffs)
        diffs = await linear.list_diffs(caller)
        print(f"list_diffs: {len(diffs)} diff(s)")

        # get_diff -> Any
        diff = await linear.get_diff(caller, urlOrId="00000000-0000-4000-8000-000000000000")
        print(f"get_diff: {type(diff).__name__}")

        # get_diff_threads -> Any
        diff_threads = await linear.get_diff_threads(
            caller, urlOrId="00000000-0000-4000-8000-000000000000"
        )
        print(f"get_diff_threads: {type(diff_threads).__name__}")

        # --- 8. Releases ----------------------------------------------------
        # list_release_pipelines -> list  (unwrap: releasePipelines)
        pipelines = await linear.list_release_pipelines(caller, type="scheduled")
        print(f"list_release_pipelines: {len(pipelines)} pipeline(s)")

        # list_releases -> list  (unwrap: releases)
        releases = await linear.list_releases(caller)
        print(f"list_releases: {len(releases)} release(s)")

        # get_release -> Any
        release = await linear.get_release(caller, id="00000000-0000-4000-8000-000000000000")
        print(f"get_release: {type(release).__name__}")

        # list_release_notes -> list  (unwrap: releaseNotes)
        release_notes = await linear.list_release_notes(caller)
        print(f"list_release_notes: {len(release_notes)} note(s)")

        # get_release_note -> Any
        release_note = await linear.get_release_note(
            caller, id="00000000-0000-4000-8000-000000000000"
        )
        print(f"get_release_note: {type(release_note).__name__}")

        # --- 9. Attachments / agent skills ----------------------------------
        # get_attachment -> Any
        attachment = await linear.get_attachment(
            caller, id="00000000-0000-4000-8000-000000000000"
        )
        print(f"get_attachment: {type(attachment).__name__}")

        # list_agent_skills -> list  (unwrap: agentSkills)
        agent_skills = await linear.list_agent_skills(caller)
        print(f"list_agent_skills: {len(agent_skills)} skill(s)")

        # get_agent_skill -> Any
        agent_skill = await linear.get_agent_skill(
            caller, id="00000000-0000-4000-8000-000000000000"
        )
        print(f"get_agent_skill: {type(agent_skill).__name__}")

        # --- 10. Utilities ---------------------------------------------------
        # extract_images -> Any  (pure markdown parse, no workspace state touched)
        images = await linear.extract_images(
            caller, markdown="![diagram](https://example.com/diagram.png)"
        )
        print(f"extract_images: {type(images).__name__}")

        # search_documentation -> list[DocumentationSearchResult]
        docs = await linear.search_documentation(caller, query="issue statuses")
        print(f"search_documentation: {len(docs)} hit(s)")


if __name__ == "__main__":
    asyncio.run(main())
