# Session Overview: github MCP Server

## Run Metadata

- **Executed:** 2026-06-19T22:46:03Z
- **Duration:** 8m 33s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The GitHub Copilot MCP server (`https://api.githubcopilot.com/mcp/`) exposes **44 tools** covering repository operations, issue and PR management, search, releases, tags, collaborators, teams, secret scanning, and code review. Authentication uses a Bearer token (`GITHUB_PAT`).

## Tools Probed vs Skipped

- **Probed (26 tools):** All non-mutating read-only tools.
- **Skipped (18 tools, mutating):** `add_comment_to_pending_review`, `add_issue_comment`, `add_reply_to_pull_request_comment`, `create_branch`, `create_or_update_file`, `create_pull_request`, `create_repository`, `delete_file`, `fork_repository`, `issue_write`, `merge_pull_request`, `pull_request_review_write`, `push_files`, `request_copilot_review`, `run_secret_scanning`, `sub_issue_write`, `update_pull_request`, `update_pull_request_branch`.

## Discriminator Candidates

The `mcpgen list` output flagged 14 discriminator candidates. After Pass 1 auto-disqualification, all were discarded as pagination/identity/filter params (`after`, `body`, `head`, `issue_number`, `message`, `organization`, `perPage`, `sha`, `since`, `state`, `tag`, `title`) or params spanning only mutating tools. Two functionally important discriminators were handled manually: `method` on `issue_read` (4 enum values: get, get_comments, get_sub_issues, get_labels) and `method` on `pull_request_read` (9 enum values). These were multi-probed but the divergent response shapes (single issue dict vs list of comments) widened to `Any` — correctly left as `return_model: null`.

## Interesting Responses and Shape Decisions

**`get_me`** — no-arg, returns a clean dict with `login`, `id`, `profile_url`, `avatar_url`, plus a nested `details` block. Shaped as `GitHubUser` using top-level scalar fields.

**`get_latest_release` / `get_release_by_tag`** — both return the same rich release object with 16+ stable top-level scalar fields. Shared `return_model: "Release"` with identical `fields` — no collision. `get_tag` returns a lighter `{ref, url, object, node_id}` structure shaped as `GitTag`.

**`list_issues`** — unusually, the response is wrapped in a `{issues: [...], totalCount: int, pageInfo: {...}}` envelope, unlike `list_pull_requests` which returns a bare list. Unwrap set to `["issues"]` with `return_container: "list"` and model `IssueSummary`.

**`issue_read` and `pull_request_read`** — both are method-discriminated and returned `_observed_shape: "Any"` after multi-probe across divergent method values. The `pull_request_read` probe with `method=get` for PR #247000 also returned a string (formatted markdown text), suggesting the server renders some methods as text. Left as `return_model: null`.

**`list_issue_types`** — returned `"str"` rather than a list. This is likely an error or formatted text from the `microsoft` org. Left as plain `Any`.

**`list_repository_collaborators`** — returned `"str"`. The endpoint for `microsoft/vscode` likely returned a formatted message rather than a JSON list, possibly due to organization privacy settings. Left as `Any`.

**`get_file_contents`** — returns raw file contents as `"str"` (the README.md content). No TypedDict warranted for a text blob.

**`get_team_members`** — returned `NoneType` for `microsoft/vscode`. This is likely an access restriction (org team membership not accessible with the PAT's scope). Marked `_probe_status: "inconclusive"` to distinguish from a genuine `None`-returning tool.

**`list_issue_fields`** — returned `[<empty>]`. Inner element shape unobservable; `microsoft` org may not have custom issue fields configured. Left as `return_container: "list"` with no model.

**Search tools** (`search_code`, `search_commits`, `search_issues`, `search_pull_requests`, `search_repositories`, `search_users`) — all return `{total_count, incomplete_results, items: [...]}` envelope. `search_code` returned empty items for `McpCaller` in `vscode`. Each shaped with a distinct result model capturing the top-level envelope scalars.

## Generated Module

Module `eval/github/github.py` (86 KB, 44 tools) parsed cleanly via `ast.parse`. Shaped tools return their `TypedDict` return models; polymorphic / str-returning / inconclusive tools remain `-> Any`. No naming collisions detected in the 17 distinct `return_model` names.
