# Session Overview — git

## Run Metadata

- **Executed:** 2026-08-25T15:44:02Z
- **Duration:** 1m 41s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## What happened

`mcpgen codegen` against the `git` server (launched via `uvx mcp-server-git`) discovered
**12 tools**. Following the read-only/mutating split (`annotations.readOnlyHint`, no
keyword fallback needed — every tool carried an explicit hint), **7 tools were probed**
live: `git_status`, `git_diff_unstaged`, `git_diff_staged`, `git_diff`, `git_log`,
`git_show`, `git_branch`. The remaining **5 were skipped as mutating**
(`readOnlyHint: false`): `git_commit`, `git_add`, `git_reset`, `git_create_branch`,
`git_checkout`. Running this repository's own working tree as the probe target
(`repo_path` = this eval repo's checkout) gave every probed tool a realistic, non-empty
payload to observe.

## Discriminator note

`mcpgen list` flagged two candidate discriminators: `branch_name` (spans `git_checkout`,
`git_create_branch` — both mutating, out of scope) and `repo_path` (spans nearly every
tool). `repo_path` matches the Pass-1 auto-disqualify pattern (path/repo identity, a
global context arg) and never appears as a key inside any observed response — it's
purely an input parameter, not a response-shape switch. No genuine discriminator applied
to the probed set.

## Shape decisions

Every probed tool returns the MCP server's `TextContent` as a **plain string** — `git`
here is a thin wrapper around the `git` CLI's stdout (status lines, unified diffs, `git
log --oneline`-style entries, `git show` patches, and `git branch -v` listings). None of
these responses are JSON-in-string (`json.loads()` was not attempted since the payloads
are visibly diff/log text, not structured data), so for all 7 tools:

- **`unwrap`**: `[]` — no envelope to strip, the string *is* the payload.
- **`return_model`**: `null` — a `TypedDict` over free-form diff/log text would be a
  false promise; the wrapper's `-> Any` (rendered as `str` at the call site) is the
  honest type.
- **`fields`**: `{}` — no stable scalar keys exist to extract from unstructured text.

Observed payload sizes ranged from tiny (`git_diff_staged`, 19 bytes — nothing staged)
to substantial (`git_diff`, ~357 KB against `HEAD~1`, and `git_show`, ~299 KB for the
`HEAD` commit) — both reflect this repo's real recent diff volume, not an anomaly.
`probed_args` (all just `repo_path` plus one discriminating field like `revision` or
`max_count`) were scrubbed to `<example-repo-path>` with `probe_args_scrubbed: true`,
since the raw value embedded this machine's local username in the filesystem path; the
gitignored `git.verify.json` sidecar retains the real path for the roundtrip verifier.

## Verification

The regenerated `eval/git/git.py` (10,115 bytes, 12 wrapper functions) parses cleanly
under `ast.parse`. `eval-kit verify git` passed all applicable checks: `ast`,
`signatures`, `idempotency` (deterministic `render_module()` re-run), and `pii` (no raw
identifiers left in the committed shapes file). `roundtrip` was skipped — expected, since
every shaped tool returns `Any`/`str` rather than a `TypedDict`, so there's no typed
non-mutating tool for the live-roundtrip check to exercise.
