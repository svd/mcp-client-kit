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

Top-level entries only, carrying the types the probe actually saw. Mark observed-`None` fields
nullable (`"benchDurationCurrent": "float | None"`).

A container field carries an element/value type only where the evidence covers every member:

| Merged `_observed_shape` for the field | `fields` value |
|---|---|
| `{"a": "str", "b": "str"}` | `dict[str, str]` |
| a mixed leaf, a nested dict/list leaf, `Any`, `str \| None`, or `...` | `dict` |
| a list whose elements are all confirmed the same scalar | `list[str]` |
| a list whose element type rests on the sampled element alone | `list` |
| `["<empty>"]` | `list`, added by hand — see SKILL.md step 4 |

Dict evidence is complete: the skeleton carries every key of that object, so one probe settles
it. **List evidence is not** — `summarize_shape` records the first element and an `...xN`
sentinel, so `list[str]` needs the raw payload
(`mcpgen probe --save-raw <server>.<tool>.probe-raw.json`, git-ignored) or a second probe
confirming the rest. Without that, `list`.

`dict[str, Any]` and `list[Any]` state exactly what `dict` and `list` state — never write them.
Neither is `dict[str, str | None]` on offer: the caller narrows a `.get()` either way.

Inside a discriminated tool the rule applies per `variants` entry, on that variant's own
probes. A generic base model (step 4 option 2) keeps a container bare unless every probed
variant showed the same element or value type.

**Nesting stops at the annotation.** Codegen emits a `TypedDict` only for a name in
`return_model` or in a `variants` entry, so `"owner": "Owner"` renders an annotation with no
class behind it — the module still imports and mypy fails instead. A nested object is `dict`,
or `dict[str, str]` when it qualifies above; when its keys are worth a reader's time, record
them in `session-overview.md`.

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
- `"probe_args_scrubbed": true` is set by `mcpgen merge` when it redacted a value; add it by
  hand when you redact one it missed. The roundtrip verifier checks the `.verify.json` sidecar
  first, so this flag matters only when the sidecar is absent or does not cover that tool.

## Scrubbing `probed_args`

`mcpgen merge` scrubs `probed_args` automatically before writing `<shapes-path>`. It replaces
email addresses, UUIDs, a leading home-directory user segment (`/Users/<name>`, `/home/<name>`,
`C:\Users\<name>`) and runs of 8+ digits with `<email>`, `<uuid>`, `<home>` and `<id>`, and sets
`"probe_args_scrubbed": true` on every entry it changed. Raw values survive in the gitignored
`.parts/` intermediates and in the gitignored `<shapes-stem>.verify.json` sidecar, which the
roundtrip verifier reads first — so scrubbing does not break verification.

**Strings only.** Non-string scalars are left alone deliberately: replacing an int account id
with a placeholder string would change the JSON type and mislead both `input_overrides` and the
verifier. A numeric id passed as an int is the known gap — check for one by hand.

**Read the merged file before committing.** The automatic pass is a floor, not a ceiling. It
cannot recognise personal names, hostnames, or bespoke internal identifiers. Replace anything
it missed and leave `probe_args_scrubbed: true` in place.

**Do NOT replace functional values** — timezone names (`"UTC"`, `"America/New_York"`), generic
table names (`"users"`, `"products"`), public repo owners/names, ISO timestamps, or standard SQL
queries. ISO timestamps survive, but an **epoch** timestamp does not: a run of 8+ digits is
indistinguishable from a numeric id, so `1756108800` becomes `<id>`. If the automatic pass
rewrote a value you need verbatim, restore it by hand from the gitignored
`<shapes-stem>.verify.json`, which holds the pre-scrub args. `--no-scrub` only helps on a merge
that still has its `.parts/` — a default merge deletes them and re-merging without parts is a
no-op, so reach for it via `mcpgen merge --keep-parts` at probe time, not afterwards.

Keep raw responses, if you want them, in `<server>.<tool>.probe-raw.json` (git-ignored) — write
one with `mcpgen probe --save-raw`, never in the shape-spec.
