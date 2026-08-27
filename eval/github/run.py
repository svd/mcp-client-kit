"""
Smoke-test runner for generated github/ wrappers.
Transport: Streamable HTTP  (https://api.githubcopilot.com/mcp/)
Auth: Bearer token (set GITHUB_PAT env var)

Args come from eval/github/github.verify.json (real, pre-scrub probe args) where
present. The discriminated tools get_commit, issue_read and pull_request_read are
absent from verify.json, so their args come from the scrubbed
github.shapes.json probed_args — those carry no placeholders, but the
issue/PR numbers (332898 / 332876) are live microsoft/vscode ids and may age out.

Usage:
    GITHUB_PAT=<token> python eval/github/run.py
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file, so its own directory goes on the
# path ahead of the package-style parent entry from the skeleton.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(1, os.path.dirname(os.path.dirname(__file__)))
import github

from mcpgen import McpBridgeCaller

SERVER_URL = "https://api.githubcopilot.com/mcp/"


async def main() -> None:
    bearer = os.environ.get("GITHUB_PAT")
    if not bearer:
        sys.exit("GITHUB_PAT not set")

    caller = McpBridgeCaller(url=SERVER_URL, bearer=bearer)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: add_comment_to_pending_review, add_issue_comment,
        # add_reply_to_pull_request_comment, create_branch, create_or_update_file,
        # create_pull_request, create_repository, delete_file, fork_repository,
        # issue_write, merge_pull_request, pull_request_review_write, push_files,
        # request_copilot_review, run_secret_scanning, sub_issue_write,
        # update_pull_request, update_pull_request_branch

        # --- identity ---------------------------------------------------
        # get_me -> AuthenticatedUser
        me = await github.get_me(caller)
        print(f"get_me: login={me.get('login')!r}  id={me.get('id')!r}")

        # get_teams -> list[TeamMembership]
        teams = await github.get_teams(caller)
        print(f"get_teams: {len(teams)} membership(s)")

        # get_team_members -> Any  (probe was inconclusive; shape unknown)
        members = await github.get_team_members(
            caller, org="EPAMHackathons", team_slug="hackathon"
        )
        print(f"get_team_members: {type(members).__name__}")

        # --- repository metadata ----------------------------------------
        # get_file_contents -> Any
        readme = await github.get_file_contents(
            caller, owner="microsoft", repo="vscode", path="README.md"
        )
        print(f"get_file_contents: {type(readme).__name__}")

        # list_branches -> list[BranchSummary]
        branches = await github.list_branches(
            caller, owner="microsoft", repo="vscode", perPage=5
        )
        print(f"list_branches: {len(branches)} branch(es)")

        # list_tags -> list[TagSummary]
        tags = await github.list_tags(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_tags: {len(tags)} tag(s)")

        # get_tag -> GitRef
        tag = await github.get_tag(
            caller, owner="microsoft", repo="vscode", tag="v1.19.3"
        )
        print(f"get_tag: ref={tag.get('ref')!r}")

        # list_repository_collaborators -> Any  (probe was inconclusive)
        collaborators = await github.list_repository_collaborators(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_repository_collaborators: {type(collaborators).__name__}")

        # --- releases ----------------------------------------------------
        # list_releases -> list[ReleaseSummary]
        releases = await github.list_releases(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_releases: {len(releases)} release(s)")

        # get_latest_release -> Release
        latest = await github.get_latest_release(
            caller, owner="microsoft", repo="vscode"
        )
        print(
            f"get_latest_release: tag_name={latest.get('tag_name')!r}"
            f"  name={latest.get('name')!r}"
        )

        # get_release_by_tag -> Release
        release = await github.get_release_by_tag(
            caller, owner="microsoft", repo="vscode", tag="1.135.0"
        )
        print(f"get_release_by_tag: tag_name={release.get('tag_name')!r}")

        # --- commits ------------------------------------------------------
        # list_commits -> list[CommitSummary]
        commits = await github.list_commits(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_commits: {len(commits)} commit(s)")

        # get_commit -> Commit  (detail="none")
        commit_none = await github.get_commit(
            caller, owner="microsoft", repo="vscode", sha="main", detail="none"
        )
        print(f"get_commit(none): sha={commit_none.get('sha')!r}")

        # get_commit -> Commit  (detail="stats" — adds nested stats+files)
        commit_stats = await github.get_commit(
            caller, owner="microsoft", repo="vscode", sha="main", detail="stats"
        )
        print(f"get_commit(stats): sha={commit_stats.get('sha')!r}")

        # get_commit -> Commit  (detail="full_patch" — adds patch per file)
        commit_patch = await github.get_commit(
            caller, owner="microsoft", repo="vscode", sha="main", detail="full_patch"
        )
        print(f"get_commit(full_patch): sha={commit_patch.get('sha')!r}")

        # --- issues -------------------------------------------------------
        # list_issue_types -> list[IssueType]
        issue_types = await github.list_issue_types(
            caller, owner="microsoft", repo="vscode"
        )
        print(f"list_issue_types: {len(issue_types)} type(s)")

        # list_issue_fields -> Any
        issue_fields = await github.list_issue_fields(
            caller, owner="microsoft", repo="vscode"
        )
        print(f"list_issue_fields: {type(issue_fields).__name__}")

        # list_issues -> list[IssueSummary]  (unwrapped from "issues")
        issues = await github.list_issues(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_issues: {len(issues)} issue(s)")

        # get_label -> Label
        label = await github.get_label(
            caller, owner="microsoft", repo="vscode", name="bug"
        )
        print(f"get_label: name={label.get('name')!r}  color={label.get('color')!r}")

        # issue_read -> Any  (method discriminator; all 5 probed variants)
        issue_get = await github.issue_read(
            caller,
            method="get",
            owner="microsoft",
            repo="vscode",
            issue_number=332898,
        )
        print(f"issue_read(get): {type(issue_get).__name__}")

        issue_comments = await github.issue_read(
            caller,
            method="get_comments",
            owner="microsoft",
            repo="vscode",
            issue_number=332898,
        )
        print(f"issue_read(get_comments): {type(issue_comments).__name__}")

        issue_sub = await github.issue_read(
            caller,
            method="get_sub_issues",
            owner="microsoft",
            repo="vscode",
            issue_number=332898,
        )
        print(f"issue_read(get_sub_issues): {type(issue_sub).__name__}")

        issue_parent = await github.issue_read(
            caller,
            method="get_parent",
            owner="microsoft",
            repo="vscode",
            issue_number=332898,
        )
        print(f"issue_read(get_parent): {type(issue_parent).__name__}")

        issue_labels = await github.issue_read(
            caller,
            method="get_labels",
            owner="microsoft",
            repo="vscode",
            issue_number=332898,
        )
        print(f"issue_read(get_labels): {type(issue_labels).__name__}")

        # --- pull requests -------------------------------------------------
        # list_pull_requests -> list[PullRequestSummary]  (state="open")
        prs_open = await github.list_pull_requests(
            caller, owner="microsoft", repo="vscode", state="open", perPage=2
        )
        print(f"list_pull_requests(open): {len(prs_open)} PR(s)")

        # list_pull_requests -> list[PullRequestSummary]  (state="closed")
        prs_closed = await github.list_pull_requests(
            caller, owner="microsoft", repo="vscode", state="closed", perPage=2
        )
        print(f"list_pull_requests(closed): {len(prs_closed)} PR(s)")

        # pull_request_read -> Any  (method discriminator; all 9 probed variants)
        pr_get = await github.pull_request_read(
            caller,
            method="get",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get): {type(pr_get).__name__}")

        pr_diff = await github.pull_request_read(
            caller,
            method="get_diff",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_diff): {type(pr_diff).__name__}")

        pr_status = await github.pull_request_read(
            caller,
            method="get_status",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_status): {type(pr_status).__name__}")

        pr_files = await github.pull_request_read(
            caller,
            method="get_files",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_files): {type(pr_files).__name__}")

        pr_commits = await github.pull_request_read(
            caller,
            method="get_commits",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_commits): {type(pr_commits).__name__}")

        pr_review_comments = await github.pull_request_read(
            caller,
            method="get_review_comments",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_review_comments): {type(pr_review_comments).__name__}")

        pr_reviews = await github.pull_request_read(
            caller,
            method="get_reviews",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_reviews): {type(pr_reviews).__name__}")

        pr_comments = await github.pull_request_read(
            caller,
            method="get_comments",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_comments): {type(pr_comments).__name__}")

        pr_check_runs = await github.pull_request_read(
            caller,
            method="get_check_runs",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get_check_runs): {type(pr_check_runs).__name__}")

        # --- search --------------------------------------------------------
        # search_repositories -> list[SearchRepositoryItem]  (unwrapped from "items")
        repos = await github.search_repositories(caller, query="vscode", perPage=3)
        print(f"search_repositories: {len(repos)} repo(s)")

        # search_code -> list[SearchCodeItem]
        code = await github.search_code(
            caller, query="repo:microsoft/vscode addEventListener", perPage=3
        )
        print(f"search_code: {len(code)} hit(s)")

        # search_commits -> list[SearchCommitItem]
        commit_hits = await github.search_commits(
            caller, query="repo:microsoft/vscode fix", perPage=3
        )
        print(f"search_commits: {len(commit_hits)} hit(s)")

        # search_issues -> list[SearchIssueItem]
        issue_hits = await github.search_issues(
            caller, query="repo:microsoft/vscode terminal rendering bug", perPage=3
        )
        print(f"search_issues: {len(issue_hits)} hit(s)")

        # search_pull_requests -> list[SearchPullRequestItem]
        pr_hits = await github.search_pull_requests(
            caller, query="repo:microsoft/vscode is:pr is:open", perPage=3
        )
        print(f"search_pull_requests: {len(pr_hits)} hit(s)")

        # search_users -> list[SearchUserItem]
        users = await github.search_users(caller, query="octocat", perPage=3)
        print(f"search_users: {len(users)} user(s)")


if __name__ == "__main__":
    asyncio.run(main())
