# openzeppelin-solidity — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:05:41Z
- **Duration:** 3m 10s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list --schema` returned **8 tools**, all of the same family: `solidity-erc20`,
`solidity-erc721`, `solidity-erc1155`, `solidity-stablecoin`, `solidity-rwa`,
`solidity-account`, `solidity-governor`, `solidity-custom`. Each is a contract
*generator* — the description for every one ends "Returns the source code of the
generated contract, formatted in a Markdown code block. Does not write to disk."

No tool ships `annotations`, so the step-2b keyword fallback decided mutability. No name
splits into a mutating whole word, and the descriptions explicitly disclaim writes, so
**all 8 were selected and probed; none were skipped.** No seed commands were configured
and none were needed.

## Discriminators

The `list` advisory flagged seven candidates: `crossChainBridging`, `decimals`, `name`,
`namespacePrefix`, `premint`, `premintChainId`, `symbol`. Pass 2 probed
`solidity-erc721` with and without `crossChainBridging="custom"`: both responses
observed as `"str"` (295 vs 311 bytes — a content difference, not a shape one). The
verdict is **inconclusive but moot** — every tool on this server returns a flat string,
so no discriminator can switch a return *shape*. Resolution taken: option 3
(unwrap-only / `Any`), which is what the honest shape already is.

## Surprises

The first `solidity-erc20` probe passed `decimals: 18` as a JSON number and came back
with `MCP error -32602: expected string, received number`. That response observes as
`"str"` and would have been indistinguishable from a real result had the raw payload not
been captured. The schema is right and the probe was wrong: `decimals`, `premint`, and
`premintChainId` are declared `"type": "string"` on the ERC-20-family tools, while
`solidity-governor` declares its own `decimals` as `"number"`. Re-probed with
schema-valid args, all 8 tools returned genuine Markdown-fenced Solidity.

Second observation: this server expresses its enums as nested `anyOf` + `const` unions
rather than an `enum` array, so codegen emits `Any` for `access`, `votes`, `upgradeable`,
and `signer` instead of `Literal[...]`. Zero `Literal` types appear in the module.

## Shape decisions

Identical for all 8 tools: `unwrap: []`, `return_model: null`, `return_container` unset,
`fields: {}`, `source: "live"`. The payload is a Markdown code block of Solidity source —
prose, not a record. The JSON-in-string test on the raw captures returned `NOT_JSON`
(`JSONDecodeError`) for both `solidity-custom` and `solidity-erc20`, confirming there is
no double-encoded record hiding inside the string. `_observed_shape: "str"` is kept as
evidence of a genuine text-returning tool — these are observed successes, not
`_probe_status: inconclusive`. `probed_args` hold only invented contract names
(`EvalToken`, `EvalGov`) and a placeholder URI; nothing required scrubbing.

## Module

`ast.parse` clean. All 8 wrappers render `-> Any`, correctly, and the module carries
embedded `__schema__` plus Args docstrings.
