# github — session overview

## Run Metadata

- **Executed:** 2026-07-14T08:27:52Z
- **Duration:** 10m 50s (wall-clock around the generate-mcp-wrappers skill run)

## Summary

The GitHub MCP server (`https://api.githubcopilot.com/mcp/`, bearer auth) exposes
44 tools. Following the subagent fallback (no `AskUserQuestion`), every non-mutating
tool was probed and every likely-mutating tool was skipped — 27 probed, 17 skipped.
Skipped tools: the keyword-flagged mutators (`create_branch`,
`create_or_update_file`, `create_pull_request`, `create_repository`, `delete_file`,
`fork_repository`, `issue_write`, `merge_pull_request`, `pull_request_review_write`,
`push_files`, `request_copilot_review`, `sub_issue_write`, `update_pull_request`,
`update_pull_request_branch`) plus three comment/reaction tools that write real
content despite not matching the mutating-keyword list (`add_comment_to_pending_review`,
`add_issue_comment`, `add_reply_to_pull_request_comment`).

All read probes ran against `microsoft/vscode` (real tag `1.128.0`, PR `#325746`,
issue `#250000` — resolved live via discovery calls rather than guessed, after an
initial guessed PR/issue number 404'd). 19 of 27 probed tools now return a shaped
`TypedDict` or `list[TypedDict]` (`CommitDetail`, `Label`, `Release` — reused for both
`get_latest_release` and `get_release_by_tag`, `GitHubUser`, `GitTag`, `TeamOrgSummary`,
`Branch`, `CommitSummary`, `IssueSummary` unwrapped from `issues`, `PullRequestSummary`,
`ReleaseSummary`, `TagSummary`, `SecretScanResult`, and six `Search*Result` envelopes
carrying `total_count`/`incomplete_results`).

Three tools returned genuine live errors rather than data and are marked
`"_probe_status": "inconclusive"`: `get_team_members` (the authenticated token has
no team memberships — `get_teams` confirmed an empty `teams: []`), `list_issue_types`
(404 — the `microsoft` org has no custom issue-types feature enabled), and
`list_repository_collaborators` (403 — token lacks the collaborators scope on
`microsoft/vscode`). `issue_read` and `pull_request_read` stay `Any`: their `method`
argument switches between dict-shaped (`get`) and list-shaped (`get_comments`,
`get_files`) responses, structurally incompatible for a single model per the
discriminator-fallback rule. `get_file_contents` stays `Any` (plain `str`, raw README
content). `list_issue_fields` returned an empty list — inner element shape
unobservable from zero samples.

The regenerated module (91,139 bytes) parses cleanly via `ast.parse`. `eval-kit
verify github` passed 4/5 checks (`ast`, `idempotency`, `pii`, `roundtrip`), with
`signatures` skipped for the same three inconclusive tools noted above — an honest,
expected skip rather than a failure.
