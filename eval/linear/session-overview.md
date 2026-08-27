# linear — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T06:09:26Z
- **Duration:** 8m 59s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list linear --schema` returned **55 tools**. Every tool carries an
`annotations.readOnlyHint`, so selection ran entirely on the primary signal: **33
read-only**, **22 mutating** (all `readOnlyHint: false` — the `save_*`, `create_*`,
`delete_*`, `merge_diff`, `share_issue`/`unshare_issue` family), skipped without a probe.
No annotation contradicted itself, and no read-only name tripped the keyword test.

Linear is hosted HTTP, so probing was serial with a 2 s interval and the read-only set was
pruned to record-carrying tools. **25 were probed**; 8 read-only tools were left unprobed:
`extract_images` and `get_attachment` are media tools whose payloads never reach the shape
(image blocks), and `get_agent_skill`, `get_release`, `get_release_note`, `get_diff`,
`get_diff_threads`, `get_milestone` each require an id this workspace has none of — their
list endpoints all returned empty, so there was no real id to pass and none was invented.

## Discriminators

The advisory named 41 shared params. Pass 1 dropped sort/window/identity forms
(`orderBy`, `createdAt`/`updatedAt`, `*Id`, `urlOrId`) and free-text content fields.
`type` survived, spanning `get_status_updates`, `list_cycles`, `list_release_pipelines`.

Pass 2 **confirmed** it on `get_status_updates`: `type="project"` returns
`{statusUpdates: [], hasNextPage: bool}`, while `type="initiative"` returns a bare string —
`Error: Initiative status updates are not enabled for this workspace.` Two shapes with no
stable shared base, so the tool resolved to **option 3, unwrap-only `Any`**. `list_cycles`
was probed across all three values (`current`/`previous`/`next`) and
`list_release_pipelines` across `scheduled` and unfiltered; every response was an
identically-shaped empty list — **inconclusive, not disproven**, and both stay `Any`.

## Shape decisions

Linear wraps list results in a single-key envelope plus `hasNextPage` (and `cursor` on
`list_issues`). Unwrapping that key with `return_container: "list"` was uniform across all
ten list tools. Eleven tools got a `TypedDict`: `Workspace`, `Team` (shared by `get_team`
and `list_teams` — identical fields), `User` (`get_user`/`list_users`), `Issue`,
`IssueSummary` (the list element lacks `attachments`/`documents`/`stateHistory`, so a
distinct name), `IssueLabel`, `IssueStatus` (`get_issue_status`/`list_issue_statuses`, a
bare top-level list), and `DocumentationSearchResult`.

The workspace is nearly empty — one team, three seeded issues, no projects, documents,
releases, diffs, or comments. Ten envelopes therefore unwrap to a list whose element shape
is unobservable; `return_model` stays `null` with an `_inner_unobserved` note rather than a
fabricated model. `get_project`, `list_milestones`, and `get_document` returned only error
payloads and are marked `_probe_status: "inconclusive"`.

Nested `priority` and `stateHistory` were left out of `fields` per the depth rule; the
`labels`/`attachments`/`documents` fields were seen only empty and are recorded as `"list"`.

The regenerated module **parsed cleanly** (`ast.parse` OK) and emits 8 `TypedDict`s;
`get_issue -> Issue`, `list_issues -> list[IssueSummary]`, `list_teams -> list[Team]`.
