# Shape-spec field reference

Per-tool entry fields in `<shapes-path>`, and the rules for filling each. Step 4 of SKILL.md
is the judgment pass; this is the field-by-field detail behind it.

## `unwrap`

The key path to the *real record*, stripping vendor envelopes. Some servers double-wrap: the
record lives under `data.entity` → `"unwrap": ["data", "entity"]`. Read `_observed_shape` to
find the level where the meaningful keys appear.

## `return_model`

The `TypedDict` name (e.g. `"Entity"`). Absent → the return stays `Any`.

- Never a Python primitive name (`str`, `int`, `list`, …) — use `null` for tools returning
  plain scalars.
- Must be a new, capitalized identifier (`CurrentTime`, `CommitSummary`) — never a Python
  keyword or builtin.
- Two tools may not share a name unless their `fields` dicts are identical. Check for
  collisions before finalising.

When several tools share a conceptual type but differ in fields, mint distinct names:

| Shape | Convention | Example |
|---|---|---|
| singular read | base name | `Release`, `Issue`, `Commit` |
| list endpoint | append `Summary` | `ReleaseSummary`, `CommitSummary` |
| search endpoint | append the verb | `SearchIssueItem`, `SearchPRItem` |

## `return_container`

Set `"list"` when the unwrapped value is a *list* of records (e.g. `query_acme`'s
`data.results`). The return type becomes `list[<model>]` and the body digs via `_dig_list`
instead of `_dig`: a list passes through, an envelope is dug, otherwise it falls back to the
last path key at top level and defaults to `[]`.

Omit for a single dict/scalar record (the `get_entity` case).

## `input_overrides`

Fix types the schema lied about. JSON Schema `number` is `float`, but some servers use `int`
for id/type fields → `{"entityType": "int"}`.

## `fields`

Keep **only top-level stable scalars the probe actually saw**, plus one exception: a
hand-added `"<field>": "list"` for a field seen only as an empty list. Mark observed-`None`
fields nullable (`"benchDurationCurrent": "float | None"`).

## `source`

`"live"`, or `"fixture"` plus a note if you authored from a recorded shape instead of a live
call. Never let a fixture fallback read as a live probe.

## Housekeeping

- Delete `_observed_shape` once you have extracted the real shape.
- Add `_mutating_suspect` / `_mutating_reason` for every probed tool flagged as mutating —
  see `references/mutating-tools.md`. Step 4 is the first point they can be added (merge
  would have replaced the entry) and they are never deleted afterwards.
- Record `"_json_unwrap": true` as a note for the next reader when a payload was
  double-encoded. Codegen does not read that key.
- `"probe_args_scrubbed": true` when a value had to be redacted. The roundtrip verifier
  checks the `.verify.json` sidecar first; this flag matters only when the sidecar is absent
  or does not cover that tool.

## Scrubbing `probed_args`

The skeleton records live `probed_args` verbatim — real ids, names, possibly PII. With
multi-probe it is a *list* of arg-dicts. Batch agents write parts with **raw** args; the
step-3 ignore preflight is what keeps them out of git.

There is exactly one scrub point: **post-merge, on the main thread, at step 4.** Open
`<shapes-path>` and replace PII after `mcpgen merge` has written both the shapes file and its
gitignored `<shapes-stem>.verify.json` sidecar. A real identifier in a version-controlled file
is a leak that survives deletion (git history) and travels to anyone the repo reaches.

**Replace only values matching a PII pattern** — email addresses, UUIDs, long numeric IDs
(8+ digits), auth tokens, personal names, or hostnames that could identify a user or system.

**Do NOT replace functional values** — timezone names (`"UTC"`, `"America/New_York"`), generic
table names (`"users"`, `"products"`), public repo owners/names, ISO timestamps, standard SQL
queries, or anything not personally identifiable. The roundtrip verifier — the `run.py` smoke
test that replays `probed_args` live — passes these to the real server, and the gitignored
`.verify.json` sidecar holds the pre-scrub args for it. Scrubbing `<shapes-path>` therefore
does not break verification.

The shape-spec records *that* `entityType` was probed as `int` and the response *shape* —
never values lifted out of the response payload. This does not empty `probed_args`: its
functional values stay. Keep raw responses, if you want them, in
`<server>.<tool>.probe-raw.json` (git-ignored), never in the shape-spec.
