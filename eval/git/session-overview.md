# Session Overview: git MCP Server

## Run Metadata

- **Executed:** 2026-06-19T22:46:13Z
- **Duration:** 2m 47s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server and Tool Inventory

The `git` MCP server (`uvx mcp-server-git`) exposes 12 tools covering the full local git workflow: status, diff (unstaged, staged, diff-to-target), log, show, branch listing, add, reset, commit, checkout, and create-branch.

Of the 12 tools, 5 were identified as mutating via name/description heuristics and skipped: `git_add`, `git_reset`, `git_commit`, `git_checkout`, and `git_create_branch`. The remaining 7 non-mutating tools were probed: `git_status`, `git_diff_unstaged`, `git_diff_staged`, `git_diff`, `git_log`, `git_show`, and `git_branch`.

## Discriminator Analysis

The `mcpgen list` advisory flagged two discriminator candidates:

- `branch_name` → `git_checkout`, `git_create_branch` — both are mutating tools and were skipped entirely, so this discriminator had no impact on the probe set.
- `repo_path` → all 12 tools — auto-disqualified by Pass 1 as a `repo_path`/path identity input-only parameter. It is a context arg (which local repository to operate on), not a response shape switch. No response-level discriminator survived to Pass 2.

## Shape Decisions

All 7 probed tools returned plain strings (`_observed_shape: "str"`). The `git` MCP server formats its output as human-readable text — a status summary, diff patch text, formatted commit log lines, branch names — rather than structured JSON. This is the expected design for a local git tool surface.

For each probed tool:

- **`git_status`**: Returns a formatted `git status` text block (e.g. "On branch eval / Your branch is ahead..."). `return_model: null`, no unwrap needed.
- **`git_diff_unstaged`** / **`git_diff_staged`** / **`git_diff`**: All return unified diff text. `return_model: null`.
- **`git_log`**: Returns formatted commit history lines with commit hash, author object repr, date, and message. Not JSON. `return_model: null`.
- **`git_show`**: Returns commit contents as formatted text. `return_model: null`.
- **`git_branch`**: Returns branch listing with `*` marking the active branch. `return_model: null`.

The JSON-unwrap check was applied: none of the string responses parsed as JSON (`json.loads()` would fail on all of them). The `_json_unwrap` annotation was not applied to any entry.

## PII Scrubbing

All `probed_args` entries contained the local filesystem path `/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval`, which includes the user's home directory name. These were replaced with `<example-repo-path>` placeholders, and `"probe_args_scrubbed": true` was added to each entry. The gitignored `git.verify.json` sidecar retains the original paths for the roundtrip verifier.

## Generated Module

The module parsed cleanly (`ast.parse` succeeded). All 12 tools were generated with correct `-> Any` return types. The 7 probed tools had their shapes confirmed as plain strings — `-> Any` is the honest return type for a text-output server, not a deficiency. No TypedDict models were emitted because no tool returned a structured dict. The generated wrappers correctly handle optional parameters (`context_lines`, `max_count`, `base_branch`, etc.) via conditional inclusion in the `args` dict.
