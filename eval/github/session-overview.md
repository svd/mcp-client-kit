# github — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:39:55Z
- **Duration:** 18m 20s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list github --schema` returned **44 tools** (the manifest note still says 35). Every
tool carries `annotations.readOnlyHint`, so classification needed no keyword heuristics:
**27 read-only, 17 mutating**. All 17 mutating tools were skipped. Hosted-HTTP probes are
serial and paced at ≥2 s, so the read-only set was pruned by one — `run_secret_scanning`
uploads file *content* to a scanner and carries no record worth a `TypedDict`.
**26 tools probed, 18 skipped.**

## Discriminators

The `list` advisory named 16 candidates. Pass 1 disqualified `after`, `head`, `perPage`, and
`since`. Seven survivors went to Pass 2 (21 paced probes, three values each): `sha`, `state`,
`tag`, `name`, `issue_number`, `pullNumber`, `base`. `name` and `tag` came back identical
(inconclusive). The other five "differed" only through **optional-field sparsity** —
`assignees` absent on an unassigned issue, `closed_at`/`merged_at` absent on an open PR,
`[<empty>]` where a branch filter matched nothing, one error string for an inaccessible `sha`.
None is response-shape polymorphism, so all five were union-merged into one `total=False`
model rather than minting variants.

The real discriminators are the `method` params, which the engine denylists and never flags:
`issue_read` (5 values) and `pull_request_read` (9 values) return genuinely unrelated payloads
per method. `get_commit.detail` (3 values) is a third. **All 17 variants were probed.**

## Shape decisions

The six `search_*` tools wrap records under `items` → `unwrap:["items"]`,
`return_container:"list"`, one `Search*Item` model each. `list_issues` is the odd one out — a
GraphQL-style `{issues, totalCount, pageInfo}` envelope → `unwrap:["issues"]`. Every other list
tool returns a bare top-level array (`unwrap:[]`). `get_latest_release` and
`get_release_by_tag` returned byte-identical shapes and share one `Release` model.

Three tools were deliberately left `Any`:

- **`issue_read` / `pull_request_read`** — every variant probed and recorded, but they mix
  `dict`, `list`, and `str` returns. `return_container` is a **top-level** shape-spec field
  shared by all variants, so a `variants` block would type the list-returning methods as the
  dict union through the impl signature. Unwrap-only `Any` (step 4 option 3) beats that lie —
  the run's most useful finding about the spec format.
- **`get_commit`** — `detail` switches the payload only by adding *nested* `stats`/`files`
  keys; `fields` promotes top-level scalars only, so three variant TypedDicts would have been
  byte-identical. Resolved as a generic base model (option 2).

## Surprises

- `get_file_contents` returns a status string plus a `resource` metadata block with the bytes
  stripped. Per the media rule it stays `Any`.
- `get_release_by_tag` 404'd on a tag from `list_tags`: vscode's release tags (`1.135.0`) are
  not its git tags (`v1.19.3`).
- `list_issue_types` 404s at org scope for `microsoft`; it shapes cleanly with `repo` supplied.
- Two tools produced no observable success payload and carry `_probe_status: inconclusive`:
  `list_repository_collaborators` (403, PAT scope) and `get_team_members` (`null`).
  `list_issue_fields` returned a genuine empty `[]`, so its element shape is unobservable — it
  is `Any` with no inconclusive marker.

`run.py` was not generated here — the harness verify stage owns it.

## Result

Module regenerated and `ast.parse`d clean (104 KB, 44 functions), imports without error, and
emits **19 TypedDicts** across **20 shaped tools**.
