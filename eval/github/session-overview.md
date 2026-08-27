# github — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T06:00:34Z
- **Duration:** 17m 23s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Coverage

The server exposes **44 tools** (the manifest's note of 35 is stale). Every tool carries
`annotations.readOnlyHint`: **27 read-only selected, 17 mutating skipped** unprobed.

One judgment call: `idempotentHint: false` appears on *all 44* tools, reads and writes alike.
Read literally it would trip the self-contradiction check on every read-only tool and drop
coverage to zero. Being uniform across the whole surface, it carries no per-tool information —
it is the MCP spec default (meaningful only when `readOnlyHint` is false), not a server
contradicting itself. Hints trusted. `destructiveHint: true` appears only on `delete_file`.

## Discriminators

Of 16 advisory candidates, Pass 1 dropped pagination/window params and the all-mutating sets.
Pass 2 probed the rest: **`sha`** (3 commits) differed only by an optional `deletions` key
nested in `files[]` — identity ref, not a discriminator. **`tag`** (3 tags) was identical:
inconclusive, resolved as one base model. **`state`** on `list_pull_requests` showed `closed`
carrying `assignees`/`closed_at`/`merged_at`/`milestone`, but `all` matched `open` — the
variance follows record content, not the argument, so option 2 (base model, `total=False`).

Two real discriminators the advisory *could not* raise, since `method` is denylisted:
**`pull_request_read`** (9 methods) and **`issue_read`** (5). All 14 variants were probed
separately. Payloads differ radically — `get` a dict, `get_files`/`get_commits`/`get_reviews`
lists, `get_diff` a bare **string**, `get_check_runs` its own envelope.

**Engine gap — the headline finding.** `_render_overloaded` reads `return_container` once per
tool and applies it to all variants, so a tool spanning dict/list/str cannot be typed
faithfully. Both were resolved to **option 3 (unwrap-only `Any`)** rather than emitting
overloads that type-error on 5 of 9 valid `pull_request_read` calls. Per-variant
`return_container` would unlock both. `get_commit.detail` (3 values) stayed one base model:
its variants differ only in non-scalar keys (`stats`, `files`) that `fields` cannot express.

## Shape decisions

**21 of 27** probed tools are typed across **20 TypedDicts** (`Release` shared by
`get_latest_release` and `get_release_by_tag`, fields verified identical).

- **Envelope unwraps:** `list_issues` → `unwrap: ["issues"]`; the six `search_*` tools →
  `unwrap: ["items"]`. All `return_container: "list"`, digging via `_dig_list`.
- **Bare lists** (`list_commits`, `list_tags`, `list_branches`, `list_releases`,
  `list_pull_requests`, `list_issue_types`, `get_teams`) need no unwrap. Notably
  `list_pull_requests` returns a bare list where `list_issues` returns an envelope — the same
  server is inconsistent between sibling tools.

Left untyped with cause: `get_file_contents` (returns `[status_string, resource-metadata]`;
file bytes never appear — media rule), `list_issue_fields` (always `[]`, inner shape
unobservable), and `get_team_members` / `list_repository_collaborators`, both marked
`_probe_status: "inconclusive"` — the first returned `null`, the second `403 Resource not
accessible by personal access token` on both `microsoft/vscode` *and* a repo the token owns,
so it is a token-scope gap. One recovery: `list_issue_types` 404s at org level but succeeds
when `repo` is supplied, turning an inconclusive entry into a real `list[IssueType]`.

## Verification

`ast.parse` clean and the module imports (104 KB, 44 wrappers). `list_issues` digs
`('issues',)`, `search_code` digs `('items',)`, bare-list tools cast directly; all TypedDicts
are `total=False`. Post-merge scrub replaced the PAT owner's login and org with
`<example-owner>`/`<example-org>` (`probe_args_scrubbed: true`), keeping public
`microsoft`/`vscode`, issue/PR numbers and SHAs as functional values; gitignored
`github.verify.json` retains the real args for roundtrip.
