# git — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:06:48Z
- **Duration:** 3m 54s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`uvx mcp-server-git` exposes **12 tools**. Every tool carries a full `annotations`
block, so step 2b's classification needed no keyword or semantic fallback:
`readOnlyHint: true` on seven — `git_status`, `git_diff_unstaged`, `git_diff_staged`,
`git_diff`, `git_log`, `git_show`, `git_branch` — and `false` on five —
`git_add`, `git_commit`, `git_reset` (also `destructiveHint: true`), `git_create_branch`,
`git_checkout`. The five mutating tools were skipped entirely and never probed.
No seed commands were configured. Transport is local `stdio`, so the full read-only
set was kept rather than pruned, and probes ran unpaced.

Probes targeted this repository's own checkout as `repo_path`; the real path is a
home directory carrying the user's name, so it was scrubbed to `/path/to/repo` in
`git.shapes.json` (`probe_args_scrubbed: true`) while the gitignored
`git.verify.json` retains the live value for roundtrip.

## Discriminators

The `list --schema` advisory named two candidates. `repo_path` (spanning all 12 tools)
is auto-disqualified by Pass 1 as path identity. `branch_name` spans only
`git_checkout` and `git_create_branch` — both mutating and outside the selected set,
so it is recorded as unresolved rather than probed. The description sweep surfaced one
extra single-tool candidate the advisory cannot see: `branch_type` on `git_branch`,
whose description enumerates `local` / `remote` / `all`. Pass 2 probed all three
holding `repo_path` fixed; all three returned the same shape (`str`, at 106 / 155 /
309 bytes). That is **inconclusive, not disproven** — but the shape is prose, so
there is nothing to overload and option 3 (unwrap-only) applies regardless.

## Shape decisions

Every one of the seven probed tools returned `_observed_shape: "str"`. The
JSON-in-string check ran against a captured raw payload for each: all seven came back
`NOT_JSON` (`JSONDecodeError`), and inspection confirmed genuine, human-readable git
output — `Repository status: On branch …`, `Commit history: Commit: '…'`, a branch
listing, unified diffs. These are real success payloads, not errors, so no
`_probe_status: inconclusive` marker was warranted anywhere. `git_diff_staged`
returned only `Staged changes:` with an empty body — an honest empty result, since
nothing was staged.

Consequently every entry keeps `unwrap: []`, `return_model: null`, `fields: {}`,
`source: "live"`. No `TypedDict` was minted, and none should be: this server is
prose-returning by design.

The regenerated module parses cleanly (`ast.parse`, 10297 bytes) with all 12 wrappers
typed from `inputSchema` and returning `Any`.
