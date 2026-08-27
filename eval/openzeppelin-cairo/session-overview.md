# openzeppelin-cairo — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:48:14Z
- **Duration:** 4m 7s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **8 tools**, all of them Cairo contract-source generators:
`cairo-erc20`, `cairo-erc721`, `cairo-erc1155`, `cairo-account`, `cairo-multisig`,
`cairo-governor`, `cairo-vesting`, `cairo-custom`.

No tool carries `annotations`, so classification fell back to the keyword plus semantic read.
Every description ends with the same sentence — *"Returns the source code of the generated
contract, formatted in a Markdown code block. Does not write to disk."* The `cairo-*` verbs
("Make a …") read as mutating on keywords alone, but the explicit *does not write to disk*
contract settles it: these are pure template renderers. **All 8 were selected and probed; none
were skipped.** No seed commands were configured, and none were needed.

## Discriminators

The `list --schema` advisory flagged six candidates: `name` (all 8 tools), `symbol`, `appName`,
`appVersion` (3 tools each), `baseUri`, and `decimals` (2 each). Pass 1 disqualified none by
name.

Pass 2 ran on `name` — the broadest candidate — against `cairo-custom`, probing three distinct
values (`MyContract`, `Vault`, `Registry`) as separate paced invocations. All three returned
`_observed_shape: "str"` (1733 / 1728 / 1731 bytes; the byte deltas track the contract name's
length, not the shape). By the strict rule this is **inconclusive, not disproven** — but it is
moot here: the response is a bare scalar string, so there is no structure for any discriminator
to switch. All six candidates are content parameters that steer the rendered source text, not
the response shape. Resolved as **option 3, unwrap-only**, and recorded as unconfirmed.

## Shape decisions

Identical for all 8 tools: `unwrap: []`, `return_model: null`, `fields: {}`.

The JSON-in-string test on the raw `cairo-custom` payload returned `NOT_JSON` — the body is a
literal ` ```cairo ` fenced block of Starknet source, not a double-encoded record. That is an
expected outcome, not a probe failure, so `_observed_shape: "str"` stands and no model was
invented. `_observed_shape` / `_observed_bytes` were kept as evidence that these are genuine
text-returning tools rather than inconclusive probes.

Every probe returned a real success payload (1663–5936 bytes). `cairo-vesting`, the smallest,
was spot-checked raw and contains `LinearVestingSchedule`, matching its probed `schedule:
"linear"`. **No `_probe_status: inconclusive` markers were needed.** `probed_args` are synthetic
placeholders throughout — no PII to scrub.

## Verification

`ast.parse` clean. All 8 wrappers are `-> Any`, no `TypedDict`s and no `_dig` emitted — correct,
since nothing has an envelope. Input typing did benefit: enum params render as
`Literal['stark', 'eth']` and `Literal['linear', 'custom']`. This server is
**`no_shaped_tool_by_design`**: every tool returns prose, so `Any` is the honest return type.
