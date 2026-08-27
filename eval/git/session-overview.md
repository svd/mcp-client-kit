# git — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T06:01:09Z
- **Duration:** 2m 39s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcp-server-git` (stdio, `uvx mcp-server-git`, no auth) exposes **12 tools**. Every one
carries a full `annotations` block, so tool selection took the primary path and the
keyword heuristic was never needed. Seven tools declare `readOnlyHint: true` and were
probed: `git_status`, `git_diff_unstaged`, `git_diff_staged`, `git_diff`, `git_log`,
`git_show`, `git_branch`. Five declare `readOnlyHint: false` and were skipped without a
live call.

The contradiction check cleared all seven: each pairs `readOnlyHint: true` with
`destructiveHint: false` and `idempotentHint: true`, and no name passes the keyword
test. No seed commands were configured, and none were needed — the eval repo itself is
a populated git repository, so every read tool had real data to return.

## mutating-skipped

- `git_commit` — `readOnlyHint: false`
- `git_add` — `readOnlyHint: false`
- `git_reset` — `readOnlyHint: false`, `destructiveHint: true`
- `git_create_branch` — `readOnlyHint: false`
- `git_checkout` — `readOnlyHint: false`

## Discriminators

The `list --schema` advisory raised two candidates. `repo_path` is auto-disqualified by
Pass 1 as path identity. `branch_name` spans only `git_checkout` and `git_create_branch`
— both mutating, both outside the selected set — so it is recorded unresolved rather
than probed. Pass 2 made no calls.

## Shape decisions

Every one of the seven probes returned a payload, and every payload observed as bare
`"str"`. This server is prose-only by design: it returns formatted git porcelain, not
records. Each response opens with a human-readable label — `Repository status:`,
`Unstaged changes:`, `Commit history:`, `Diff with main:`.

Because a bare `"str"` is also what a double-encoded record looks like, the JSON-in-string
test was run on all seven raw payloads captured via `mcpgen call --out`. All seven
returned `NOT_JSON` (`JSONDecodeError`). There is no envelope to unwrap and no record to
model, so `unwrap` stays empty and `return_model` stays `null` across the board — the
generated `-> Any` is the honest signature, not a coverage gap. No tool was recorded as
`_probe_status: inconclusive`: every probe genuinely succeeded.

One incidental observation: `git_show` leaks a Python repr into its text
(`Author: <git.Actor "...">`) rather than formatting the author. That is a server-side
quirk, not a shaping decision.

`probed_args` held the absolute repo path, which embeds a username; all seven entries
were scrubbed to `<example-repo-path>` and marked `probe_args_scrubbed: true`. The
gitignored `git.verify.json` sidecar retains the real path for roundtrip.

The regenerated module parses cleanly (`ast.parse` OK, 10298 bytes, 12 functions).
