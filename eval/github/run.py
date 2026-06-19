"""
Smoke-test runner for generated github/ wrappers.
Transport: HTTP  (https://api.githubcopilot.com/mcp/)
Auth: Bearer token (set GITHUB_PAT env var)

Usage:
    GITHUB_PAT=<token> python github/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import github

from mcpgen import McpBridgeCaller

SERVER_URL = "https://api.githubcopilot.com/mcp/"


async def main() -> None:
    bearer = os.environ.get("GITHUB_PAT")
    if not bearer:
        sys.exit("GITHUB_PAT not set")

    caller = McpBridgeCaller(url=SERVER_URL, bearer=bearer)

    # Skipped mutating tools: add_comment_to_pending_review, add_issue_comment,
    # add_reply_to_pull_request_comment, create_branch, create_or_update_file,
    # create_pull_request, create_repository, delete_file, fork_repository,
    # issue_write, merge_pull_request, pull_request_review_write, push_files,
    # request_copilot_review, run_secret_scanning, sub_issue_write,
    # update_pull_request, update_pull_request_branch

    # get_me -> GitHubUser
    me = await github.get_me(caller)
    print(f"get_me: login={me.get('login')!r}  id={me.get('id')!r}")

    # get_teams -> Any
    teams = await github.get_teams(caller)
    print(f"get_teams: {type(teams).__name__}")

    # get_team_members -> Any  (probe_status=inconclusive: may return quota/auth error)
    team_members = await github.get_team_members(caller, org="microsoft", team_slug="vscode")
    print(f"get_team_members: {type(team_members).__name__}")

    # list_branches -> list[Branch]
    branches = await github.list_branches(caller, owner="microsoft", repo="vscode", perPage=5)
    print(f"list_branches: {len(branches)} item(s)")

    # list_commits -> list[CommitSummary]
    commits = await github.list_commits(caller, owner="microsoft", repo="vscode", perPage=5)
    print(f"list_commits: {len(commits)} item(s)")

    # list_tags -> list[TagSummary]
    tags = await github.list_tags(caller, owner="microsoft", repo="vscode", perPage=5)
    print(f"list_tags: {len(tags)} item(s)")

    # get_commit -> CommitDetail
    commit = await github.get_commit(caller, owner="microsoft", repo="vscode", sha="main", detail="stats")
    print(f"get_commit: sha={commit.get('sha')!r}  html_url={commit.get('html_url')!r}")

    # get_file_contents -> Any
    file_contents = await github.get_file_contents(caller, owner="microsoft", repo="vscode", path="README.md")
    print(f"get_file_contents: {type(file_contents).__name__}")

    # get_label -> Label
    label = await github.get_label(caller, owner="microsoft", repo="vscode", name="bug")
    print(f"get_label: name={label.get('name')!r}  color={label.get('color')!r}")

    # get_latest_release -> Release
    latest_release = await github.get_latest_release(caller, owner="microsoft", repo="vscode")
    print(f"get_latest_release: tag_name={latest_release.get('tag_name')!r}  name={latest_release.get('name')!r}")

    # get_release_by_tag -> Release
    release_by_tag = await github.get_release_by_tag(caller, owner="microsoft", repo="vscode", tag="1.100.2")
    print(f"get_release_by_tag: tag_name={release_by_tag.get('tag_name')!r}  published_at={release_by_tag.get('published_at')!r}")

    # get_tag -> GitTag
    git_tag = await github.get_tag(caller, owner="microsoft", repo="vscode", tag="1.100.2")
    print(f"get_tag: ref={git_tag.get('ref')!r}")

    # list_releases -> list[ReleaseSummary]
    releases = await github.list_releases(caller, owner="microsoft", repo="vscode", perPage=5)
    print(f"list_releases: {len(releases)} item(s)")

    # list_issues -> list[IssueSummary]
    issues = await github.list_issues(caller, owner="microsoft", repo="vscode", perPage=3, state="OPEN")
    print(f"list_issues: {len(issues)} item(s)")

    # list_pull_requests -> list[PullRequestSummary]
    prs = await github.list_pull_requests(caller, owner="microsoft", repo="vscode", perPage=3, state="open")
    print(f"list_pull_requests: {len(prs)} item(s)")

    # list_repository_collaborators -> Any
    collabs = await github.list_repository_collaborators(caller, owner="microsoft", repo="vscode", perPage=3)
    print(f"list_repository_collaborators: {type(collabs).__name__}")

    # list_issue_fields -> Any  (list container)
    issue_fields = await github.list_issue_fields(caller, owner="microsoft")
    print(f"list_issue_fields: {type(issue_fields).__name__}")

    # list_issue_types -> Any
    issue_types = await github.list_issue_types(caller, owner="microsoft")
    print(f"list_issue_types: {type(issue_types).__name__}")

    # issue_read -> Any  (method=get)
    issue_get = await github.issue_read(
        caller, method="get", owner="microsoft", repo="vscode", issue_number=248765
    )
    print(f"issue_read(get): {type(issue_get).__name__}")

    # issue_read -> Any  (method=get_comments)
    issue_comments = await github.issue_read(
        caller, method="get_comments", owner="microsoft", repo="vscode", issue_number=248765, perPage=3
    )
    print(f"issue_read(get_comments): {type(issue_comments).__name__}")

    # pull_request_read -> Any  (method=get)
    pr_get = await github.pull_request_read(
        caller, method="get", owner="microsoft", repo="vscode", pullNumber=247000
    )
    print(f"pull_request_read(get): {type(pr_get).__name__}")

    # pull_request_read -> Any  (method=get_files)
    pr_files = await github.pull_request_read(
        caller, method="get_files", owner="microsoft", repo="vscode", pullNumber=247000, perPage=3
    )
    print(f"pull_request_read(get_files): {type(pr_files).__name__}")

    # search_repositories -> SearchReposResult
    repos = await github.search_repositories(caller, query="vscode language:typescript stars:>10000", perPage=3)
    print(f"search_repositories: total_count={repos.get('total_count')}  incomplete_results={repos.get('incomplete_results')}")

    # search_code -> SearchCodeResult
    code = await github.search_code(caller, query="McpCaller repo:microsoft/vscode", perPage=3)
    print(f"search_code: total_count={code.get('total_count')}  incomplete_results={code.get('incomplete_results')}")

    # search_commits -> SearchCommitsResult
    search_commits_result = await github.search_commits(caller, query="repo:microsoft/vscode fix bug", perPage=3)
    print(f"search_commits: total_count={search_commits_result.get('total_count')}  incomplete_results={search_commits_result.get('incomplete_results')}")

    # search_issues -> SearchIssuesResult
    issues_search = await github.search_issues(caller, query="repo:microsoft/vscode is:issue label:bug", perPage=3)
    print(f"search_issues: total_count={issues_search.get('total_count')}  incomplete_results={issues_search.get('incomplete_results')}")

    # search_pull_requests -> SearchPRsResult
    prs_search = await github.search_pull_requests(caller, query="repo:microsoft/vscode is:pr is:open", perPage=3)
    print(f"search_pull_requests: total_count={prs_search.get('total_count')}  incomplete_results={prs_search.get('incomplete_results')}")

    # search_users -> SearchUsersResult
    users = await github.search_users(caller, query="svd", perPage=3)
    print(f"search_users: total_count={users.get('total_count')}  incomplete_results={users.get('incomplete_results')}")


if __name__ == "__main__":
    asyncio.run(main())
