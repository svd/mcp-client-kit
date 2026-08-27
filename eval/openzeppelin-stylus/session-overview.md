# openzeppelin-stylus — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:07:40Z
- **Duration:** 2m 20s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Engine

`uv run mcpgen` (0.9.0.dev1), config form via `MCPGEN_SERVERS=.mcp.eval.json`, transport `http`, auth `none`.

## Tools

The server exposes **3 tools**: `stylus-erc20`, `stylus-erc721`, `stylus-erc1155`. All three
were probed; none were skipped.

No tool carries `annotations`, so mutation classification fell to the keyword test plus a
semantic read. No name matches a mutating verb, and every description ends with the
sentence "Does not write to disk." — the server is a contract-source generator, not a
writer. `mutating-skipped`: none.

Seed commands: none were configured, and none are meaningful here — the server holds no
store to seed.

## Discriminators

`mcpgen list --schema` flagged one candidate: `name` → `stylus-erc1155`, `stylus-erc20`,
`stylus-erc721`. It survives Pass 1 (it is not on the engine denylist and is not a
pagination, sort, or path-identity form), so Pass 2 ran: three separate paced probes of
`stylus-erc20` with `name` = `MyToken`, `GoldCoin`, `AlphaBeta`. All three observed the
identical shape `"str"`; only `_observed_bytes` moved (2557 / 2563 / 2569), tracking the
contract name's length inside the generated source rather than any structural change.

Per the skill this is **inconclusive, not disproven**, so all three tools stay
polymorphic-suspect. The point is moot in practice: the resolution taken is option 3
(unwrap-only `Any`), which is what a suspect tool would fall back to anyway. `name` is a
free-text contract identifier interpolated into the emitted Rust, not a shape switch.

## Shape decisions

Nothing was shapeable, and that is the honest result rather than a coverage gap. Each
tool returns a single Markdown code fence wrapping a Rust source file — 2.0–2.6 KB of
prose. The raw payload of all three was captured with `mcpgen call` and inspected: each
opens `` ```rust `` followed by `// SPDX-License-Identifier: MIT`, so none is an error
message masquerading as text. The JSON-in-string test on `stylus-erc20` returned
`NOT_JSON` (`JSONDecodeError`) — the payload is genuinely prose, not a double-encoded
record.

Accordingly every entry keeps `unwrap: []`, `return_model: null`, `fields: {}`, and
`source: "live"`. `_observed_shape: "str"` is left in place as evidence of a real
text-returning tool; `_probe_status` was deliberately **not** set to `inconclusive`,
because every probe returned an observable success payload.

`probed_args` needed no scrubbing — the invented contract names (`AlphaBeta`, `MyNft`,
`MyMulti`) are functional values, not PII.

## Verification

The regenerated module parses cleanly (`ast.parse` OK). All three wrappers read `-> Any`,
which matches the shape-spec exactly. `run.py` is the harness's responsibility and was not
generated here.
