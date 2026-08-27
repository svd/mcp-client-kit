# openzeppelin-cairo — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:06:59Z
- **Duration:** 3m 36s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list --schema` returned **8 tools**, all of them contract-source generators:
`cairo-erc20`, `cairo-erc721`, `cairo-erc1155`, `cairo-account`, `cairo-multisig`,
`cairo-governor`, `cairo-vesting`, `cairo-custom`. No tool carries an `annotations`
block, so the keyword + semantic fallback decided mutability. **Nothing was flagged
mutating**: no tool name contains a mutating whole word (`cairo-*` prefix plus a
standard name), and every description ends with the same sentence — *"Returns the
source code of the generated contract, formatted in a Markdown code block. Does not
write to disk."* All 8 were selected and all 8 were probed; none skipped.

Enum-constrained params were probed with their first declared value
(`cairo-account.type="stark"`, `cairo-vesting.schedule="linear"`). Codegen rendered
those and `cairo-governor`'s four enums as `Literal[...]` automatically.

## Discriminators

The `list` advisory raised six candidates: `appName`, `appVersion`, `baseUri`,
`decimals`, `name`, `symbol`. All six survived Pass 1 — none is on the engine denylist
and none matches the camelCase pagination/sort/path additions. Pass 2 probed
`cairo-erc20` at three distinct `decimals` values (`"6"`, `"18"`, `"0"`), holding
`name` and `symbol` fixed: **all three observed identically as `"str"`** (2344 / 2285 /
2344 bytes — a byte-count difference, not a shape difference). That is *inconclusive,
not disproven*, so the sibling tools stay polymorphic-suspect on paper. Resolution is
**option 3 (unwrap-only / `Any`)** and is not a compromise here: these parameters
change the *content* of the emitted Cairo source, never its container. A plain scalar
return has no dict variants to discriminate over.

## Shape decisions

Every probe returned a genuine success payload — a fenced ` ```cairo ` block of real
Starknet contract source, 1.6 KB to 5.9 KB. The `_observed_shape == "str"` path
triggered the JSON-in-string test; a raw `call --out` capture on `cairo-erc20` came back
`NOT_JSON` (`JSONDecodeError`), confirming prose rather than a double-encoded record.
So for all 8 entries: `unwrap: []`, `return_model: null`, `fields: {}`, `source:
"live"`, `_observed_shape: "str"` retained as evidence. No `_probe_status:
"inconclusive"` marker applies — these are observed successes, not error-only responses.

Nothing needed scrubbing: `probed_args` hold only invented contract names (`MyToken`,
`MyGovernor`), an `example.com` base URI, and functional scalars.

## Verification

The regenerated module is 41 470 bytes, `ast.parse` clean, exporting 8 async wrappers
that all correctly read `-> Any` with zero `TypedDict` classes. This is the honest
outcome — a server whose entire surface returns prose is a true `no_shaped_tool_by_design`
N/A for roundtrip, not a coverage gap.
