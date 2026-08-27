# huggingface — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:06:01Z
- **Duration:** 3m 27s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list huggingface --schema` returned **4 tools**: `hf_whoami`, `hub_repo_search`,
`hub_repo_details`, and `hf_fs`. Every one carries `annotations.readOnlyHint: true` and
`destructiveHint: false`, so step 2b cleared all four on annotations alone — no keyword or
semantic fallback was needed and **no tool was skipped as mutating**. All four were selected
and probed. The server is hosted HTTP, so probes ran serially with ≥2 s pacing and no fan-out.

## Discriminators

The `list --schema` stderr advisory did **not** fire: no scalar parameter is shared by two or
more tools under the same name, so its precondition was never met. The description sweep found
three candidates anyway, all confined to a single tool:

- `hub_repo_details.repo_type` — scalar enum `model|dataset|space`, plus `operations`
  (array enum `overview|dataset_structure|dataset_preview`).
- `hub_repo_search.repo_types` — **array** enum, so no set of `Literal` overloads can describe
  it (one call may request several types at once).
- `hf_fs.operations[].cmd` — enum `ls|cat|attach|stat|find|search`, nested inside an
  array-of-objects, so it is not an overload discriminator either.

Pass 2 probed variants live: `hub_repo_details` at `repo_type=model` and `repo_type=dataset`
(with `dataset_structure`), and `hf_fs` at `cmd=ls` and `cmd=stat`. Every variant returned the
same shape class, so the candidates are **inconclusive, not disproven** — but the reason makes
resolution moot (below).

## Shape decisions

Every tool on this server returns a single Markdown document, not a structured payload.
`_observed_shape` came back `"str"` for all four. The guarded JSON-in-string test was run on all
four raw captures (`hf_whoami`, `hub_repo_search`, `hub_repo_details`, `hf_fs-ls`) and every one
reported `NOT_JSON` — the payloads are human-readable prose: headed sections and bullet lists for
`hf_whoami`/`hub_repo_details`/`hub_repo_search`, a pipe-delimited Markdown table for `hf_fs ls`.

So for all four: `unwrap: []`, `return_model: null`, `fields: {}` — option 3 (unwrap-only) for
the discriminated tools, which is the honest outcome regardless of variant, since no variant
carries a record to model. These are genuine text-returning tools, **not** inconclusive probes:
each call returned a real success payload, so no `_probe_status` marker was added.

One surprise worth recording: `hf_whoami` succeeded anonymously (no auth configured for this
server) and answered with a rate-limit advisory rather than an account record. That is a valid
result for the tool's stated purpose, not an error.

`repo_type`, `operations`, `repo_types`, and `sort` all carry scalar `enum`s and codegen rendered
them as `Literal[...]` automatically — no hand-widening was applied.

## Verification

The regenerated module parses cleanly (`ast.parse`, 10666 bytes). All four wrappers read `-> Any`,
which is correct here: this server has no shaped tool by design. Runner generation was left to the
harness verify stage per the eval-harness rule.
