# github — session overview

## Run Metadata

- **Executed:** 2026-08-25T15:43:44Z
- **Duration:** 7m 44s (wall-clock around the generate-mcp-wrappers skill run)

## Summary

The GitHub MCP server (`https://api.githubcopilot.com/mcp/`, bearer auth) exposes 44
tools — unchanged in count from the prior eval run, though several tool schemas have
shifted upstream since then (`get_file_contents` gained a `fields` filter param,
`add_issue_comment.comment_id` tightened from `number` to `integer`,
`create_or_update_file` gained `allow_symlink_write`, `search_pull_requests` gained a
`fields`/`sort`/`order` surface). Running as a non-interactive subagent, every
non-mutating tool (`readOnlyHint` / keyword+semantic fallback) was probed live and
every mutating tool was skipped — 27 probed, 17 skipped. Skipped: the keyword-flagged
mutators (`create_branch`, `create_or_update_file`, `create_pull_request`,
`create_repository`, `delete_file`, `fork_repository`, `issue_write`,
`merge_pull_request`, `pull_request_review_write`, `push_files`,
`request_copilot_review`, `sub_issue_write`, `update_pull_request`,
`update_pull_request_branch`), plus three comment/reaction tools that write real
content despite not matching the mutating-keyword list (`add_comment_to_pending_review`,
`add_issue_comment`, `add_reply_to_pull_request_comment`).

All read probes ran against `microsoft/vscode` (tag `1.128.0`, PR `#332572`, issue
`#250000` — the PR number was rediscovered live via `list_pull_requests` this run,
since the previously-recorded PR had aged out of the "open" filter). 19 of 27 probed
tools return a shaped `TypedDict` or `list[TypedDict]`: `CommitDetail`, `Label`,
`Release` (shared by `get_latest_release`/`get_release_by_tag`), `GitHubUser`,
`GitTag`, `TeamOrgSummary`, `Branch`, `CommitSummary`, `IssueSummary` (unwrapped from
`issues`), `PullRequestSummary`, `ReleaseSummary`, `TagSummary`, `SecretScanResult`,
and six `Search*Result` envelopes carrying `total_count`/`incomplete_results`
(`search_pull_requests` additionally exposes a `search_type` field observed for the
first time this run).

Three tools returned genuine live errors and are marked `"_probe_status":
"inconclusive"`: `list_issue_types` (404 — `microsoft` org has no custom issue-types
feature enabled), `list_repository_collaborators` (403 — token lacks the collaborators
scope on `microsoft/vscode`), and `get_team_members` (the guessed `team_slug: "vscode"`
does not resolve to a real team — `get_teams` was re-probed with no `user` arg this
run and confirmed the authenticated token belongs to org `EPAMHackathons` with zero
team memberships, so no real team slug is discoverable to re-probe against). `get_teams`
itself is now correctly modeled as `list[TeamOrgSummary]` from that self-scoped call
(the prior run's arg of `user: "torvalds"` returned a bare `null` for a foreign user
and was corrected). `issue_read` and `pull_request_read` stay `Any`: their `method`
argument switches between dict-shaped (`get`) and list-shaped (`get_comments`,
`get_files`) responses, structurally incompatible for a single model per the
discriminator-fallback rule. `get_file_contents` stays `Any` (plain `str`, raw README
content). `list_issue_fields` returned an empty list — inner element shape remains
unobservable from zero samples.

The regenerated module (100,991 bytes) parses cleanly via `ast.parse`. `eval-kit
verify github` passed 4/5 checks (`ast`, `idempotency`, `pii`, `roundtrip`), with
`signatures` skipped for the three inconclusive tools noted above — an honest,
expected skip rather than a failure. Final verdict: **pass**.
