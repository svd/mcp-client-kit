# Session Overview: git MCP Server

## Run Metadata

- **Executed:** 2026-07-14T08:28:13Z
- **Duration:** 3m 33s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server and Tool Inventory

The `git` MCP server (`uvx mcp-server-git`) exposes 12 tools covering the local git workflow: status, diff (unstaged, staged, diff-to-target), log, show, branch listing, add, reset, commit, checkout, and create-branch.

5 tools were judged mutating and skipped without probing: `git_add`, `git_reset`, `git_commit`, `git_checkout`, `git_create_branch`. Most don't literally match the mutating-keyword heuristic, but their descriptions ("Records changes to the repository", "Switches branches", etc.) make plain a live call would mutate this repo's working tree or history, so they were excluded on judgment. The remaining 7 read-only tools were probed against this repo itself (`repo_path` = the eval-kit working directory): `git_status`, `git_diff_unstaged`, `git_diff_staged`, `git_diff`, `git_log`, `git_show`, `git_branch`.

## Discriminator Analysis

`mcpgen list` flagged two candidates: `branch_name` (spans only the two skipped mutating tools, so never reached a probe) and `repo_path` (spans all 12 tools but is auto-disqualified under Pass 1 as a path/repo-identity input parameter — it selects which repository to operate on, not the response shape). No true response-shape discriminator survived to step 4.

## Shape Decisions

All 7 probed tools returned plain strings (`_observed_shape: "str"`), confirmed by direct raw-payload calls for `git_status`, `git_log`, and `git_branch` — each response was human-readable text (a status block, formatted commit-log lines with an `Actor` repr, a `*`-marked branch list), none of which parsed as JSON, so the JSON-unwrap check didn't apply to any tool.

Per-tool decisions (all `unwrap: []`, `return_model: null`, `fields: {}` — no structured record to extract): `git_status` returns a formatted status block; `git_diff_unstaged`/`git_diff_staged`/`git_diff` return unified diff text; `git_log` returns formatted commit-history lines (hash, author repr, date, message); `git_show` returns commit contents as text; `git_branch` returns a newline-separated, `*`-marked branch listing.

This matches the server's design: a human-readable text surface over local git rather than a JSON API, so `-> Any` is the honest, final shape here — not a gap to close.

## PII Scrubbing

Every `probed_args` entry carried the real absolute repo path, which embeds the local username. Each was replaced with `<example-repo-path>` and `"probe_args_scrubbed": true` was set. The gitignored `git.verify.json` sidecar retains the real path for a future roundtrip run. Raw payload dumps used to confirm string-vs-JSON content were deleted after inspection rather than left in the tree, since `git_log`'s raw output embeds a commit author's name and email.

## Generated Module and Verification

The regenerated module parsed cleanly (`ast.parse` succeeded, 12 async functions), all carrying `-> Any` — correct, since no tool produced a structured shape. `eval-kit verify git` reported `ast pass`, `signatures pass`, `idempotency pass`, `pii pass`, `roundtrip skip` (`no_shaped_non_mutating_tool`), for an overall verdict of **pass**.
