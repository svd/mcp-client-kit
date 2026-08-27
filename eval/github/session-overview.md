# github — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:06:25Z
- **Duration:** 12m 29s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **44 tools**; `annotations.readOnlyHint` cleared **27**. Because this is a
hosted HTTP endpoint (every probe is serial and paced ≥ 2 s), the set was pruned to the 26
record-carrying read-only tools. `run_secret_scanning` was the only read-only tool dropped: it
requires the caller to supply file content, so it is a scanner rather than a record source. The
17 mutating tools were never called. No seed commands were configured.

## Discriminators

The `list --schema` advisory named 16 candidates; all were disqualified as pagination
(`perPage`, `after`, `since`), sort/filter (`state`, `base`, `head`), object identity
(`issue_number`, `pullNumber`, `tag`, `name`, `sha`), or mutating-only params. The three real
discriminators came from the **description sweep**, and all three were invisible to the
advisory by construction:

- **`method` on `issue_read`** (5 values) and **`pull_request_read`** (7 values) — `method` sits
  in the engine denylist. Both are dramatic shape switches, and every value was probed
  separately. Resolved with overloads (option 1).
- **`detail` on `get_commit`** (none/stats/full_patch) — single-tool scoped, so no shared-param
  advisory could fire. All three probed and confirmed: `none` drops the top-level `stats` and
  `files` keys; `full_patch` adds `patch` per file. Resolved as a **generic base model**
  (option 2) rather than overloads, because `detail` is optional and codegen emits a
  discriminator without a default — overloads would have made it mandatory on every call. The
  varying keys are dict/list and never enter `fields`, so nothing is lost.

## Shape decisions

Envelope unwrapping: all six `search_*` tools unwrap `["items"]` → `list[Search…Item]`;
`list_issues` unwraps `["issues"]` → `list[IssueSummary]`. The `list_*` tools return bare JSON
arrays, so they take `return_container: "list"` with no unwrap path. Singular reads map to plain
`TypedDict`s (`Commit`, `Release`, `Tag`, `Label`, `AuthenticatedUser`). `get_latest_release` and
`get_release_by_tag` share the `Release` model — identical observed fields.

**25 TypedDicts** were emitted in total.

## Surprises and honest gaps

- `list_repository_collaborators` returned a 403 (`Resource not accessible by personal access
  token`) — no payload was ever observed, recorded as `_probe_status: inconclusive`.
- `get_teams` returned one org with an empty `teams` array, so no real `team_slug` existed;
  `get_team_members` answered `null` and is likewise `inconclusive`. Its `probed_args` org and
  slug were scrubbed.
- `get_file_contents` is polymorphic on `path`: a file yields `[prose str, resource-metadata
  dict]` with the bytes dropped by MCP, a directory yields `list[dir-entry]`. `path` is free
  text, so no Literal overloads are possible — left as `Any` (option 3).
- `list_issue_fields` returned `[]` for microsoft/vscode; element shape unobservable.
- Per-variant containers are not expressible in the shape-spec (`return_container` is
  tool-level), so the list-returning and `get_diff` string variants of `issue_read` /
  `pull_request_read` stay `Any` rather than be mistyped as dicts.

The regenerated module **parsed cleanly** (`ast.parse` OK, 103.9 KB, 44 wrappers). Runner
generation was left to the harness verify stage as instructed.
