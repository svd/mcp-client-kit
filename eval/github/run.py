"""
Smoke-test runner for generated github/ wrappers.
Transport: Streamable HTTP  (https://api.githubcopilot.com/mcp/)
Auth: Bearer token (set GITHUB_PAT env var)

Usage:
    GITHUB_PAT=<token> python eval/github/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module lives at eval/github/github.py, inside a directory that is
# itself named "github". A plain `import github` with eval/ on sys.path would
# resolve to that directory as a namespace package instead of the module, so
# load the file by path as `github_wrappers`.
_spec = importlib.util.spec_from_file_location(
    "github_wrappers",
    os.path.join(os.path.dirname(__file__), "github.py"),
)
github_wrappers = importlib.util.module_from_spec(_spec)
sys.modules["github_wrappers"] = github_wrappers
_spec.loader.exec_module(github_wrappers)

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
        # request_copilot_review, sub_issue_write, update_pull_request,
        # update_pull_request_branch.
        #
        # Args below are the real probed args from github.verify.json.

        # --- identity / org context -------------------------------------
        # get_me -> AuthenticatedUser
        me = await github_wrappers.get_me(caller)
        print(f"get_me: login={me.get('login')!r}  id={me.get('id')!r}")

        # get_teams -> list[TeamGroup]
        teams = await github_wrappers.get_teams(caller)
        print(f"get_teams: {len(teams)} org group(s)")

        # get_team_members -> Any
        # Probe was inconclusive (no success payload observed), so the shape is
        # unknown and the call may fail against a token without org team scope.
        members = await github_wrappers.get_team_members(
            caller, org="EPAMHackathons", team_slug="engineering"
        )
        print(f"get_team_members: {type(members).__name__}")

        # --- repository discovery ---------------------------------------
        # search_repositories -> list[SearchRepositoryItem]
        repos = await github_wrappers.search_repositories(
            caller, query="vscode", perPage=3
        )
        print(f"search_repositories: {len(repos)} repo(s)")

        # list_repository_collaborators -> Any
        # Probe was inconclusive (no success payload observed); shape unknown.
        collaborators = await github_wrappers.list_repository_collaborators(
            caller, owner="svd", repo="ubiquity-4konverta", perPage=3
        )
        print(f"list_repository_collaborators: {type(collaborators).__name__}")

        # --- branches / commits -----------------------------------------
        # list_branches -> list[BranchSummary]
        branches = await github_wrappers.list_branches(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_branches: {len(branches)} branch(es)")

        # list_commits -> list[CommitSummary]
        commits = await github_wrappers.list_commits(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_commits: {len(commits)} commit(s)")

        # get_commit -> Commit
        commit = await github_wrappers.get_commit(
            caller,
            owner="microsoft",
            repo="vscode",
            sha="84ef3481c697a6bdf3bdb5777c50ba54346a1afe",
        )
        print(f"get_commit: sha={commit.get('sha')!r}  url={commit.get('html_url')!r}")

        # --- tags / releases ---------------------------------------------
        # list_tags -> list[TagSummary]
        tags = await github_wrappers.list_tags(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_tags: {len(tags)} tag(s)")

        # get_tag -> TagRef
        tag = await github_wrappers.get_tag(
            caller, owner="microsoft", repo="vscode", tag="v1.19.3"
        )
        print(f"get_tag: ref={tag.get('ref')!r}")

        # list_releases -> list[ReleaseSummary]
        releases = await github_wrappers.list_releases(
            caller, owner="microsoft", repo="vscode", perPage=3
        )
        print(f"list_releases: {len(releases)} release(s)")

        # get_latest_release -> Release
        latest = await github_wrappers.get_latest_release(
            caller, owner="microsoft", repo="vscode"
        )
        print(
            f"get_latest_release: tag_name={latest.get('tag_name')!r}  "
            f"name={latest.get('name')!r}"
        )

        # get_release_by_tag -> Release
        release = await github_wrappers.get_release_by_tag(
            caller, owner="microsoft", repo="vscode", tag="1.135.0"
        )
        print(
            f"get_release_by_tag: tag_name={release.get('tag_name')!r}  "
            f"draft={release.get('draft')!r}"
        )

        # --- issue metadata ----------------------------------------------
        # list_issue_types -> list[IssueType]
        issue_types = await github_wrappers.list_issue_types(
            caller, owner="microsoft", repo="vscode"
        )
        print(f"list_issue_types: {len(issue_types)} type(s)")

        # list_issue_fields -> Any  (returned [] on every probe; element shape unknown)
        issue_fields = await github_wrappers.list_issue_fields(
            caller, owner="microsoft", repo="vscode"
        )
        print(f"list_issue_fields: {type(issue_fields).__name__}")

        # get_label -> Label
        label = await github_wrappers.get_label(
            caller, owner="microsoft", repo="vscode", name="bug"
        )
        print(f"get_label: name={label.get('name')!r}  color={label.get('color')!r}")

        # --- issues -------------------------------------------------------
        # list_issues -> list[IssueSummary]
        issues = await github_wrappers.list_issues(
            caller, owner="microsoft", repo="vscode", state="OPEN", perPage=3
        )
        print(f"list_issues: {len(issues)} issue(s)")

        # issue_read -> Any  (discriminator=method; only the 'get' variant was
        # probed, so the other methods are not exercised here)
        issue = await github_wrappers.issue_read(
            caller,
            method="get",
            owner="microsoft",
            repo="vscode",
            issue_number=332877,
        )
        print(f"issue_read(get): {type(issue).__name__}")

        # --- pull requests -------------------------------------------------
        # list_pull_requests -> list[PullRequestSummary]
        prs = await github_wrappers.list_pull_requests(
            caller, owner="microsoft", repo="vscode", state="all", perPage=3
        )
        print(f"list_pull_requests: {len(prs)} PR(s)")

        # pull_request_read -> Any  (discriminator=method; only the 'get' variant
        # was probed, so the other methods are not exercised here)
        pr = await github_wrappers.pull_request_read(
            caller,
            method="get",
            owner="microsoft",
            repo="vscode",
            pullNumber=332876,
        )
        print(f"pull_request_read(get): {type(pr).__name__}")

        # --- file contents --------------------------------------------------
        # get_file_contents -> Any  (resource/media envelope: the file bytes are
        # never in the payload, only status + resource metadata)
        contents = await github_wrappers.get_file_contents(
            caller, owner="microsoft", repo="vscode", path="README.md"
        )
        print(f"get_file_contents: {type(contents).__name__}")

        # --- cross-repo search ------------------------------------------------
        # search_code -> list[SearchCodeItem]
        code_hits = await github_wrappers.search_code(
            caller, query="language:python def main", perPage=3
        )
        print(f"search_code: {len(code_hits)} hit(s)")

        # search_commits -> list[SearchCommitItem]
        commit_hits = await github_wrappers.search_commits(
            caller, query="repo:microsoft/vscode fix", perPage=3
        )
        print(f"search_commits: {len(commit_hits)} hit(s)")

        # search_issues -> list[SearchIssueItem]
        issue_hits = await github_wrappers.search_issues(
            caller,
            query="terminal crash",
            owner="microsoft",
            repo="vscode",
            perPage=3,
        )
        print(f"search_issues: {len(issue_hits)} hit(s)")

        # search_pull_requests -> list[SearchPullRequestItem]
        pr_hits = await github_wrappers.search_pull_requests(
            caller, query="repo:microsoft/vscode is:pr", perPage=3
        )
        print(f"search_pull_requests: {len(pr_hits)} hit(s)")

        # search_users -> list[SearchUserItem]
        user_hits = await github_wrappers.search_users(
            caller, query="torvalds", perPage=3
        )
        print(f"search_users: {len(user_hits)} hit(s)")

        # --- secret scanning (read-only: scans the supplied text, writes nothing)
        # run_secret_scanning -> SecretScanResult
        scan = await github_wrappers.run_secret_scanning(
            caller,
            owner="microsoft",
            repo="vscode",
            files=(
                "AKIAIOSFODNN7EXAMPLE\n"
                "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
            ),
        )
        print(
            f"run_secret_scanning: blobsScanned={scan.get('blobsScanned')!r}  "
            f"secrets={len(scan.get('secrets') or [])}"
        )


if __name__ == "__main__":
    asyncio.run(main())
