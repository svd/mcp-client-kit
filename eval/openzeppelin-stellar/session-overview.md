# openzeppelin-stellar — skill run overview

## Run Metadata

- **Executed:** 2026-08-27T11:12:25Z
- **Duration:** 2m 20s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list --schema` returned **6 tools**, all of them contract-source generators:
`stellar-account`, `stellar-fungible`, `stellar-governor`, `stellar-non-fungible`,
`stellar-stablecoin`, `stellar-vault`. No tool carries `annotations`, so classification fell
back to the keyword-plus-semantic read. Every description ends with the same sentence —
"Returns the source code of the generated contract, formatted in a Markdown code block. Does
not write to disk." — which settles the mutating question for the whole surface: these are
pure functions over their arguments. **All 6 were probed, 0 skipped.** No seed commands were
configured and none were run.

## Discriminators

`list` raised four candidates on stderr: `decimals` (2 tools), `name` (6 tools), `premint`
(2 tools), `symbol` (4 tools). None survives a semantic read — each is a value stamped into
the emitted Rust, not a switch over response shape. The description sweep found no parameter
naming a response key. Pass 2 ran anyway against `stellar-fungible`, comparing
`{name, symbol}` against `{name, symbol, decimals: "18", premint: "1000"}`: both returned
`str` (607 B vs 692 B). Formally inconclusive, but moot — a discriminator cannot turn a
string into a record, and the whole surface is strings.

## Shape decisions

Every probe returned a genuine success payload: `_observed_shape: "str"` at 607–2183 bytes,
with sizes tracking contract complexity (the smart-account and governor templates are the
largest). The raw payload for `stellar-fungible` was captured via `mcpgen call --out` and run
through the JSON-in-string guard, which reported **NOT_JSON** — the body is a literal
` ```rust ` fence wrapping Soroban source, not a double-encoded record. So for all six tools:
`unwrap: []`, `return_model: null`, `fields: {}`. There is nothing to unwrap and no
`TypedDict` to honestly emit. `_observed_shape` was kept as evidence that the `str` is real
rather than an unobserved shape; no `_probe_status: inconclusive` marker was added, because
no probe failed.

`probed_args` needed no scrubbing — every value is an invented contract name or ticker
(`MyToken`, `MTK`, `MyVault`), and the server takes no ids, paths, or credentials.

## Outcome

The regenerated module is 29,114 bytes, parses cleanly under `ast.parse`, and every wrapper
returns `Any`. That is the correct result here, not a coverage gap: this server is
`no_shaped_tool_by_design`. `run.py` was left to the harness verify stage.
