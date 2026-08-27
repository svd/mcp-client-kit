"""
Smoke-test runner for generated github/ wrappers.
Transport: Streamable HTTP  (https://api.githubcopilot.com/mcp/)
Auth: Bearer token (set GITHUB_PAT env var)

Args come from eval/github/github.verify.json (real, pre-scrub probe args).
Three tools carry a shape discriminator and are called once per probed variant:
  - issue_read        (method: get / get_comments / get_sub_issues / get_parent / get_labels)
  - pull_request_read (method: get / get_diff / get_status / get_files / get_commits /
                       get_review_comments / get_reviews)
  - get_commit        (detail: none / stats / full_patch — see `_discriminator_note`
                       in github.shapes.json; resolved as one base model, but all
                       three response shapes were probed, so all three are exercised)
get_file_contents is polymorphic on `path` (file vs. directory), so it is called
once per probed path.

get_team_members probed against a private org; github.verify.json holds the real
org/team but this file is committed, so those two values come from the environment
(GITHUB_TEAM_ORG / GITHUB_TEAM_SLUG) and the call is skipped when they are unset.

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

# Real values lifted from github.verify.json.
OWNER = "microsoft"
REPO = "vscode"
COMMIT_SHA = "a3b089bdf6dd50bf85586c39b01f345628b25dfa"
FILE_PATH = "README.md"
DIR_PATH = "src/"
LABEL_NAME = "bug"
RELEASE_TAG = "1.135.0"
GIT_TAG = "vsda-v1.39.1"
ISSUE_NUMBER = 332915
PULL_NUMBER = 331934
PER_PAGE = 3


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

        # --- identity / org context -------------------------------------------
        # get_me -> AuthenticatedUser
        me = await github.get_me(caller)
        print(f"get_me: login={me.get('login')!r}  id={me.get('id')!r}")

        # get_teams -> list[OrgTeams]
        teams = await github.get_teams(caller)
        print(f"get_teams: {len(teams)} org team group(s)")

        # get_team_members -> Any  (probe was inconclusive; real org/team are
        # private, so they come from the environment rather than this file)
        team_org = os.environ.get("GITHUB_TEAM_ORG")
        team_slug = os.environ.get("GITHUB_TEAM_SLUG")
        if team_org and team_slug:
            members = await github.get_team_members(
                caller, org=team_org, team_slug=team_slug
            )
            print(f"get_team_members: {type(members).__name__}")
        else:
            print("get_team_members: skipped (GITHUB_TEAM_ORG/GITHUB_TEAM_SLUG unset)")

        # --- repository metadata ----------------------------------------------
        # list_branches -> list[Branch]
        branches = await github.list_branches(caller, owner=OWNER, repo=REPO, perPage=PER_PAGE)
        print(f"list_branches: {len(branches)} branch(es)")

        # list_tags -> list[TagSummary]
        tags = await github.list_tags(caller, owner=OWNER, repo=REPO, perPage=PER_PAGE)
        print(f"list_tags: {len(tags)} tag(s)")

        # get_tag -> Tag
        tag = await github.get_tag(caller, owner=OWNER, repo=REPO, tag=GIT_TAG)
        print(f"get_tag: ref={tag.get('ref')!r}  node_id={tag.get('node_id')!r}")

        # list_releases -> list[ReleaseSummary]
        releases = await github.list_releases(caller, owner=OWNER, repo=REPO, perPage=PER_PAGE)
        print(f"list_releases: {len(releases)} release(s)")

        # get_latest_release -> Release
        latest = await github.get_latest_release(caller, owner=OWNER, repo=REPO)
        print(f"get_latest_release: tag_name={latest.get('tag_name')!r}  name={latest.get('name')!r}")

        # get_release_by_tag -> Release
        release = await github.get_release_by_tag(caller, owner=OWNER, repo=REPO, tag=RELEASE_TAG)
        print(f"get_release_by_tag: tag_name={release.get('tag_name')!r}  draft={release.get('draft')!r}")

        # list_repository_collaborators -> Any  (probe inconclusive: the PAT has
        # no push access to microsoft/vscode, so no success payload was observed)
        collaborators = await github.list_repository_collaborators(
            caller, owner=OWNER, repo=REPO, perPage=PER_PAGE
        )
        print(f"list_repository_collaborators: {type(collaborators).__name__}")

        # --- file contents (polymorphic on `path`) ----------------------------
        # get_file_contents -> Any  (file path -> [prose str, resource metadata])
        file_contents = await github.get_file_contents(
            caller, owner=OWNER, repo=REPO, path=FILE_PATH
        )
        print(f"get_file_contents({FILE_PATH!r}): {type(file_contents).__name__}")

        # get_file_contents -> Any  (directory path -> list[dir-entry dict])
        dir_contents = await github.get_file_contents(
            caller, owner=OWNER, repo=REPO, path=DIR_PATH
        )
        print(f"get_file_contents({DIR_PATH!r}): {type(dir_contents).__name__}")

        # --- commits -----------------------------------------------------------
        # list_commits -> list[CommitSummary]
        commits = await github.list_commits(caller, owner=OWNER, repo=REPO, perPage=PER_PAGE)
        print(f"list_commits: {len(commits)} commit(s)")

        # get_commit -> Commit  (detail='none': no stats/files keys)
        commit_none = await github.get_commit(
            caller, owner=OWNER, repo=REPO, sha=COMMIT_SHA, detail="none"
        )
        print(
            f"get_commit(none): sha={commit_none.get('sha')!r}  "
            f"has_stats={'stats' in commit_none}"
        )

        # get_commit -> Commit  (detail='stats': server default)
        commit_stats = await github.get_commit(
            caller, owner=OWNER, repo=REPO, sha=COMMIT_SHA, detail="stats"
        )
        print(
            f"get_commit(stats): sha={commit_stats.get('sha')!r}  "
            f"files={len(commit_stats.get('files') or [])}"
        )

        # get_commit -> Commit  (detail='full_patch': adds `patch` per file)
        commit_full = await github.get_commit(
            caller, owner=OWNER, repo=REPO, sha=COMMIT_SHA, detail="full_patch"
        )
        print(
            f"get_commit(full_patch): sha={commit_full.get('sha')!r}  "
            f"files={len(commit_full.get('files') or [])}"
        )

        # --- issues ------------------------------------------------------------
        # list_issue_types -> list[IssueType]
        issue_types = await github.list_issue_types(caller, owner=OWNER, repo=REPO)
        print(f"list_issue_types: {len(issue_types)} type(s)")

        # list_issue_fields -> Any  (returned [] for microsoft/vscode: no custom fields)
        issue_fields = await github.list_issue_fields(caller, owner=OWNER, repo=REPO)
        print(f"list_issue_fields: {type(issue_fields).__name__}")

        # get_label -> Label
        label = await github.get_label(caller, owner=OWNER, repo=REPO, name=LABEL_NAME)
        print(f"get_label: name={label.get('name')!r}  color={label.get('color')!r}")

        # list_issues -> list[IssueSummary]
        issues = await github.list_issues(caller, owner=OWNER, repo=REPO, perPage=PER_PAGE)
        print(f"list_issues: {len(issues)} issue(s)")

        # issue_read -> Issue  (method='get')
        issue = await github.issue_read(
            caller, method="get", owner=OWNER, repo=REPO, issue_number=ISSUE_NUMBER
        )
        print(
            f"issue_read(get): number={issue.get('number')!r}  "
            f"state={issue.get('state')!r}  comments={issue.get('comments')!r}"
        )

        # issue_read -> Any  (method='get_comments': bare JSON list)
        issue_comments = await github.issue_read(
            caller, method="get_comments", owner=OWNER, repo=REPO,
            issue_number=ISSUE_NUMBER, perPage=PER_PAGE,
        )
        print(f"issue_read(get_comments): {type(issue_comments).__name__}")

        # issue_read -> Any  (method='get_sub_issues': bare JSON list, [] when probed)
        sub_issues = await github.issue_read(
            caller, method="get_sub_issues", owner=OWNER, repo=REPO,
            issue_number=ISSUE_NUMBER,
        )
        print(f"issue_read(get_sub_issues): {type(sub_issues).__name__}")

        # issue_read -> IssueParent  (method='get_parent')
        issue_parent = await github.issue_read(
            caller, method="get_parent", owner=OWNER, repo=REPO, issue_number=ISSUE_NUMBER
        )
        print(f"issue_read(get_parent): parent={issue_parent.get('parent')!r}")

        # issue_read -> IssueLabels  (method='get_labels')
        issue_labels = await github.issue_read(
            caller, method="get_labels", owner=OWNER, repo=REPO, issue_number=ISSUE_NUMBER
        )
        print(f"issue_read(get_labels): totalCount={issue_labels.get('totalCount')!r}")

        # --- pull requests -----------------------------------------------------
        # list_pull_requests -> list[PullRequestSummary]
        prs = await github.list_pull_requests(caller, owner=OWNER, repo=REPO, perPage=PER_PAGE)
        print(f"list_pull_requests: {len(prs)} pull request(s)")

        # pull_request_read -> PullRequest  (method='get')
        pr = await github.pull_request_read(
            caller, method="get", owner=OWNER, repo=REPO, pullNumber=PULL_NUMBER
        )
        print(
            f"pull_request_read(get): number={pr.get('number')!r}  "
            f"state={pr.get('state')!r}  merged={pr.get('merged')!r}"
        )

        # pull_request_read -> Any  (method='get_diff': plain unified-diff string)
        pr_diff = await github.pull_request_read(
            caller, method="get_diff", owner=OWNER, repo=REPO, pullNumber=PULL_NUMBER
        )
        print(f"pull_request_read(get_diff): {type(pr_diff).__name__}")

        # pull_request_read -> PullRequestStatus  (method='get_status')
        pr_status = await github.pull_request_read(
            caller, method="get_status", owner=OWNER, repo=REPO, pullNumber=PULL_NUMBER
        )
        print(
            f"pull_request_read(get_status): state={pr_status.get('state')!r}  "
            f"total_count={pr_status.get('total_count')!r}"
        )

        # pull_request_read -> Any  (method='get_files': bare JSON list)
        pr_files = await github.pull_request_read(
            caller, method="get_files", owner=OWNER, repo=REPO,
            pullNumber=PULL_NUMBER, perPage=PER_PAGE,
        )
        print(f"pull_request_read(get_files): {type(pr_files).__name__}")

        # pull_request_read -> Any  (method='get_commits': bare JSON list)
        pr_commits = await github.pull_request_read(
            caller, method="get_commits", owner=OWNER, repo=REPO,
            pullNumber=PULL_NUMBER, perPage=PER_PAGE,
        )
        print(f"pull_request_read(get_commits): {type(pr_commits).__name__}")

        # pull_request_read -> PullRequestReviewThreads  (method='get_review_comments')
        pr_review_comments = await github.pull_request_read(
            caller, method="get_review_comments", owner=OWNER, repo=REPO,
            pullNumber=PULL_NUMBER,
        )
        print(
            "pull_request_read(get_review_comments): "
            f"totalCount={pr_review_comments.get('totalCount')!r}"
        )

        # pull_request_read -> Any  (method='get_reviews': bare JSON list)
        pr_reviews = await github.pull_request_read(
            caller, method="get_reviews", owner=OWNER, repo=REPO, pullNumber=PULL_NUMBER
        )
        print(f"pull_request_read(get_reviews): {type(pr_reviews).__name__}")

        # --- search ------------------------------------------------------------
        # search_repositories -> list[SearchRepositoryItem]
        repos = await github.search_repositories(
            caller, query="language:python mcp", perPage=PER_PAGE
        )
        print(f"search_repositories: {len(repos)} repo(s)")

        # search_code -> list[SearchCodeItem]
        code_hits = await github.search_code(
            caller, query="repo:microsoft/vscode ripgrep", perPage=PER_PAGE
        )
        print(f"search_code: {len(code_hits)} hit(s)")

        # search_commits -> list[SearchCommitItem]
        commit_hits = await github.search_commits(
            caller, query="repo:microsoft/vscode fix", perPage=PER_PAGE
        )
        print(f"search_commits: {len(commit_hits)} hit(s)")

        # search_issues -> list[SearchIssueItem]
        issue_hits = await github.search_issues(
            caller, query="repo:microsoft/vscode terminal rendering bug", perPage=PER_PAGE
        )
        print(f"search_issues: {len(issue_hits)} hit(s)")

        # search_pull_requests -> list[SearchPRItem]
        pr_hits = await github.search_pull_requests(
            caller, query="repo:microsoft/vscode is:merged", perPage=PER_PAGE
        )
        print(f"search_pull_requests: {len(pr_hits)} hit(s)")

        # search_users -> list[SearchUserItem]
        users = await github.search_users(caller, query="octocat", perPage=PER_PAGE)
        print(f"search_users: {len(users)} user(s)")


if __name__ == "__main__":
    asyncio.run(main())
