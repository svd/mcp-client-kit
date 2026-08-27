# git — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:40:01Z
- **Duration:** 5m 16s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcp-server-git` exposes **12 tools**, and every one of them carries a full `annotations`
block, so step 2b needed no keyword or semantic fallback. `readOnlyHint` split the surface
cleanly: **7 read-only** (`git_status`, `git_diff`, `git_diff_staged`, `git_diff_unstaged`,
`git_log`, `git_show`, `git_branch`) and **5 mutating** (`git_add`, `git_commit`, `git_reset`,
`git_create_branch`, `git_checkout`). The mutating five were skipped entirely — `git_reset`
also flags `destructiveHint: true`. Transport is local `stdio`, so the full read-only set was
kept rather than pruned, and all 7 were probed against this repository's own checkout in one
unpaced batch. No seed commands were configured or needed: the repo already held commits,
branches, and an unstaged working tree.

## Discriminators

The `list --schema` advisory raised two candidates. **`repo_path`** (spanning all 12 tools) is
auto-disqualified by Pass 1 as path identity — it selects *which* repository is read, never the
response shape. **`branch_name`** survived Pass 1 but spans only `git_checkout` and
`git_create_branch`, both mutating and both outside the selected set; it is recorded here as
**unresolved, never probed**. No candidate touched a selected tool, so Pass 2 had nothing to
confirm.

## Probe results and shape decisions

Every one of the 7 probes returned a **successful, non-empty payload**, and every one observed
as `"str"` — 106 bytes for `git_branch` up to 651 KB for `git_diff`. That is not a probe
failure and not an inconclusive result: `mcp-server-git` formats each response as
human-readable prose for a model to read, not as a record. `git_status` returns
`"Repository status:\n..."`, `git_branch` returns `git branch` output verbatim, and `git_log`
returns a `"Commit history:"` block whose author line is a repr of a `git.Actor` object.

Per step 3's JSON-in-string check, the raw payloads for `git_status`, `git_branch`, and
`git_log` — the three most plausibly structured — were captured with `call --out` and tested:
all three came back **`NOT_JSON`**. There is no double-encoded record to unwrap. Every entry
therefore keeps `unwrap: []`, `return_model: null`, and empty `fields`, with `_observed_shape`
retained as evidence that the verdict was observed rather than assumed. `probed_args` was
scrubbed — the absolute `repo_path` carries a username — and `probe_args_scrubbed: true` set;
the gitignored `git.verify.json` holds the real path for the roundtrip verifier.

## Module

The regenerated `git.py` parses cleanly (`ast.parse` OK, 10388 bytes) with all 12 wrappers
typed from `inputSchema` and returning `Any`. Zero shaped tools is the honest outcome for this
server, not a coverage gap.
