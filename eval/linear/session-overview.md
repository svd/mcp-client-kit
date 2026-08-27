# linear — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:49:25Z
- **Duration:** 10m 18s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list linear --schema` returned **55 tools**, every one carrying `annotations`. The
split was clean enough that the keyword fallback never ran: **33 `readOnlyHint: true`** and
**22 `readOnlyHint: false`**, none unannotated. All 33 read-only tools were selected and
probed; all 22 mutating tools (`save_*`, `delete_*`, `create_*`, `merge_diff`, `share_issue`,
`submit_diff_review`, `resolve_diff_thread`) were skipped entirely and never called. No seed
commands were configured for this server, so none ran.

## Discriminators

The `list` advisory flagged 45 candidates, mostly noise. Pass 1 disqualified `orderBy`,
`createdAt`/`updatedAt` (date-window filters), and the group spanning only mutating tools.
Two reached Pass 2:

- **`id`** (8 read-only tools). Probed `get_issue` at three real issue keys; all three shapes
  were byte-identical. Per the skill this is **inconclusive, not disproven** — recorded as
  unconfirmed. `Issue` is emitted as the observed shape, not promoted to a confident variant model.
- **`type`** on `get_status_updates` (required, `enum: [project, initiative]`, only 2 values).
  `type=project` returned `{statusUpdates: [], hasNextPage}`; `type=initiative` returned an
  error — *"Initiative status updates are not enabled for this workspace"*. One value observable,
  the other unobservable, so the candidate stays unconfirmed and `get_status_updates` is left
  unwrap-only.

## Surprises

The workspace is nearly empty, which dominated the run. Only issues, teams, users, labels,
and issue statuses hold data; projects, documents, releases, release notes, release
pipelines, diffs, agent skills, project labels, cycles, and comments all returned `[]`. Their **envelope** shape is typed as an unwrap path, but the inner element
shape is unobservable — no model was fabricated from zero samples.

Ten getters had no object to fetch and returned errors, not records. Two error styles appeared: a structured envelope
(`{error, message, status, requestId}` — `get_document`, `get_attachment`, `get_agent_skill`)
and a bare prose string (`get_release`, `get_project`, `get_diff`, …). Neither is an observed
shape, so all ten carry
`"_probe_status": "inconclusive"`.

**Schema lie:** `list_comments` declares `required: []`, but the server rejects an argument-free
call with *"Provide exactly one of issueId, projectId, initiativeId, documentId, milestoneId, or
statusUpdateId"*. The constraint lives only in per-field descriptions. Re-probed with a real
`issueId`.

`extract_images` returned prose ("No Linear upload images found") — a genuine text response,
not an error, so it stays `Any` unmarked.

## Shape decisions

11 tools were shaped into 9 `TypedDict`s. `get_issue`/`list_issues` split into `Issue` and `IssueSummary`
because the singular adds `attachments`, `documents`, `stateHistory`; `get_user`/`list_users`
split the same way over `teams`. `Team` and `IssueStatus` are each shared by a list and a
singular endpoint with identical fields. Nine date fields seen only as `null` are typed
`Any | None` rather than guessed as `str`; fields seen only as `[]` stay bare `list`. The nested
`priority` dict was left out rather than modelled from one probe.

`ast.parse` succeeds on the regenerated module; shaped tools return `list[IssueSummary]`,
`Issue`, `list[DocumentationHit]` etc. and unwrap via `_dig_list`. `run.py` is the harness
verify stage's job and was not generated here.
