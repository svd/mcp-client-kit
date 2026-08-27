# huggingface — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:39:52Z
- **Duration:** 5m 19s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list huggingface --schema` returned **4 tools**, all carrying
`annotations.readOnlyHint: true` and `destructiveHint: false`: `hf_whoami`,
`hub_repo_search`, `hub_repo_details`, and `hf_fs`. No mutating tools exist on this
endpoint, so nothing was skipped for safety — **4 probed, 0 skipped**. The surface is
notably consolidated: `hf_fs` is a mini shell (`ls|cat|attach|stat|find|search` over
`hf://` URIs) that subsumes what other servers spread across a dozen tools.

Discriminators: **N/A**. The advisory did not fire and the precondition confirms why —
the only scalar parameter names shared by two or more tools are `limit` and `sort`, both
on the engine's denylist. `repo_type`, `operations`, and `hf_fs`'s inner `cmd` are
per-tool (or nested inside an array item), so no cross-tool candidate exists. Their
variants were still exercised by hand, below.

## Probe results

Every probe returned a payload and every payload was **Markdown prose**, not a record:

| Tool | Probed args | Bytes | Shape |
|---|---|---|---|
| `hf_whoami` | `{}` | 321 | `str` |
| `hub_repo_search` | `query=bert, repo_types=[model], limit=3` | 1475 | `str` |
| `hub_repo_details` | model `openai/gpt-oss-120b`; dataset `stanfordnlp/imdb` (`overview`+`dataset_structure`) | 4337 | `str` |
| `hf_fs` | `ls hf://models/trending`; `stat hf://models/openai/gpt-oss-120b` | 1035 | `str` |

Because `_observed_shape == "str"` for all four, each raw payload was captured with
`mcpgen call --out` and run through the JSON-in-string guard. All five captures returned
**`NOT_JSON`** — the responses are genuinely human-readable documents (`# openai/gpt-oss-120b`,
`## Overview`, `**Downloads:** 52.9M`, `- Exists: yes`), never a double-encoded JSON record.
This is a settled fact, not a probe failure: the calls succeeded, so no `_probe_status:
inconclusive` marker applies.

Two shape-relevant variants were exercised deliberately rather than assumed.
`hub_repo_details` was probed once as a model and once as a dataset with
`operations=["overview","dataset_structure"]` — the variant most likely to carry a
structured schema payload — and both returned prose. `hf_fs` was probed with `ls` and with
`stat`; `stat`'s output is a bullet list, not the metadata dict a filesystem tool would be
expected to emit.

## Shape decisions

No tool was shaped. For all four: `unwrap: []`, `return_model: null`, `fields: {}`,
`source: "live"`. There is no envelope to dig and no record to promote — inventing an
unwrap path here would make `_dig` return a fragment of a Markdown string. Wrappers
correctly stay `-> Any`; the honest contract for this server is "returns text". Scalar
enums did render as `Literal[...]` on `repo_type`, `operations`, `sort`, and `repo_types`,
so the input side is typed even though the output side cannot be.

`probed_args` needed no scrubbing: only public repo ids and the query term `bert`, no
local paths or account identifiers. The anonymous `hf_whoami` response confirms no
credential was in play.

The regenerated module parsed cleanly (`ast.parse` OK), 4 functions, `-> Any` on each.
