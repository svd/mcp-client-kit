# linear — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:14:35Z
- **Duration:** 7m 29s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list linear --schema` returned **55 tools** over the hosted HTTP endpoint
(`https://mcp.linear.app/mcp`, OAuth — the stored token refreshed silently). Every tool supplies
`annotations.readOnlyHint`, so classification needed no keyword guessing: **33 read-only, 22
mutating**. All 22 mutating tools were skipped unprobed.

Hosted means serial, paced probes, so the read-only set was pruned to record-carrying tools:
**22 probed**, 11 unprobed. Nine (`get_document`, `get_project`, `get_release`,
`get_release_note`, `get_diff`, `get_diff_threads`, `get_agent_skill`, `list_milestones`,
`get_milestone`) need an id for an object this workspace lacks; an invented id records an error
shape, not a record. `get_attachment` and `extract_images` return media envelopes, so `-> Any`.

## Surprises

The workspace is nearly empty. Eleven list tools returned `[]` — projects, documents, diffs,
releases, release notes, release pipelines, project labels, agent skills, comments, cycles,
status updates. The envelope key is known, the record is not, so they are unwrap-only with
`return_model: null`. Re-probing a populated workspace would type them.

`get_status_updates` is genuinely discriminated on `type`. `project` returned the usual
`{statusUpdates, hasNextPage}` envelope; `initiative` returned an error string — *"Initiative
status updates are not enabled for this workspace"*. That variant is unobserved, not disproven,
so no overloads were emitted.

Two shape-controlling params are invisible to the advisory by construction: `list_issues.fields`
and `list_projects.fields` are **array**-typed projections (36 and 23 enum members). A confirming
probe (`fields: ["title","assignee"]`) narrowed the record to `{id, title}` and surfaced an extra
envelope key `cursor`. An array param selects several values at once, so overloads cannot
describe it — resolved as a **generic base model** (`total=False`). `list_cycles.type` probed
identical (empty) across `current`/`previous`; recorded unconfirmed.

## Shape decisions

Every list tool shares one envelope, `{"<key>": [...], "hasNextPage": bool}`; unwrap is that key
with `return_container: "list"`.

| Tool | unwrap | model |
|---|---|---|
| `get_workspace` | — | `Workspace` |
| `list_teams` / `get_team` | `teams` / — | `TeamSummary` / `Team` |
| `list_users` / `get_user` | `users` / — | `UserSummary` / `User` |
| `list_issues` / `get_issue` | `issues` / — | `IssueSummary` / `Issue` |
| `list_issue_statuses` / `get_issue_status` | — (bare list) / — | `IssueStatusSummary` / `IssueStatus` |
| `list_issue_labels` | `labels` | `IssueLabelSummary` |
| `search_documentation` | — (bare list) | `DocumentationHit` |
| 11 empty-list tools | their key | `null` (unobservable) |

Per the depth rule `priority` (`{value, name}`), `stateHistory`, and `get_user.teams` stayed out
of `fields`. `labels`, `attachments`, `documents` were seen only as `[]` and use the allowed
`"list"` placeholder. Ten `sla*`/`*At` fields probed `None` → `str | None`.

Probed args holding workspace UUIDs were scrubbed to placeholders; the real values live in the
gitignored `linear.verify.json`.

## Verification

The regenerated module `ast.parse`s cleanly (135,325 bytes). Eval targets spot-checked:
`list_teams(...) -> list[TeamSummary]` digging `_dig_list(result, ('teams',))`, and
`get_issue(...) -> Issue`. 11 of 22 probed tools return a `TypedDict` or `list[TypedDict]`;
the rest unwrap the envelope and honestly return `Any`.
