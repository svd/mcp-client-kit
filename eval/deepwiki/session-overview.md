# deepwiki — skill session overview

## Run Metadata

- **Executed:** 2026-08-27T11:03:14Z
- **Duration:** 3m 1s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list deepwiki --schema` returned **3 tools**, all of them read-only:
`ask_question`, `read_wiki_contents`, `read_wiki_structure`. The server ships no
`annotations` block, so classification fell back to the keyword plus semantic read of
`references/mutating-tools.md`; every name is a pure read verb (`ask`, `read`) and no tool
writes anything. Nothing was skipped as mutating, and **all 3 tools were probed**. No seed
commands were configured, and none were needed — deepwiki is a hosted read-only index over
public GitHub repositories.

## Discriminators

`list --schema` emitted no discriminator advisory. Checked by hand: the only parameter shared
by two or more tools is `repoName`, which the engine denylists (`reponame`) as an identity
param, and on `ask_question` it is an `anyOf` union (`string | array`) that fails the scalar
type test regardless. The description sweep found no parameter naming a response key. Recorded
as **discriminators: N/A**; Pass 2 was skipped.

## Probing and the surprise

All three tools were probed against `modelcontextprotocol/python-sdk` — a real, public repo, so
the required `repoName` arg referenced something that actually exists. Probes were paced ≥2 s
apart because the endpoint is hosted HTTP. Every probe succeeded and every one returned
`_observed_shape: "str"`.

That is the notable result: deepwiki returns **prose, not records**. To rule out the
double-encoding case, each tool's raw payload was captured with `mcpgen call --out` and tested
with the guarded `json.loads` snippet. All three came back `NOT_JSON`:

- `read_wiki_structure` (1.9 KB) — a plain-text outline (`Available pages for …`, numbered list)
- `read_wiki_contents` (535 KB) — one concatenated Markdown document (`# Page: Overview …`)
- `ask_question` (2.2 KB) — an English answer paragraph with inline code spans

`NOT_JSON` here is an expected outcome, not a probe failure, so no `_probe_status:
"inconclusive"` marker was added — these are genuine success payloads that happen to be text.

## Shape decisions

For all three tools: `unwrap: []`, `return_model: null`, `fields: {}`, `-> Any`. There is no
envelope to dig past and no record to model; inventing an unwrap path would make `_dig` return
a substring instead of the document. `_observed_shape: "str"` is left in the sidecar as the
evidence that the `Any` is honest rather than unresolved. `probed_args` needed no scrubbing —
a public repo slug and a benign question string, both functional.

One useful side effect: codegen read `_observed_bytes` and added a size warning to
`read_wiki_contents`'s docstring (`~522 KB observed`), which is exactly the signal a caller
needs for a tool that dumps a whole wiki into context.

## Verification

The regenerated module parses cleanly (`ast.parse` OK), carries embedded `__schema__` on each
function, and correctly types `repoName` as `str` on the two read tools and `Any` on
`ask_question` (whose union schema admits a list). `run.py` is the harness's responsibility and
was not generated here.
