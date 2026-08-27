# openzeppelin-cairo — session overview

## Run Metadata

- **Executed:** 2026-08-27T11:11:17Z
- **Duration:** 2m 39s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

`mcpgen list --schema` reported **8 tools**, all of them contract-template generators:
`cairo-erc20`, `cairo-erc721`, `cairo-erc1155`, `cairo-account`, `cairo-multisig`,
`cairo-governor`, `cairo-vesting`, `cairo-custom`. None carries `annotations`, so
classification fell back to the keyword-plus-semantic read: the verbs ("Make a fungible
token…") sound mutating, but the endpoint is a stateless Cairo source-code renderer — it
writes nothing, deploys nothing, and holds no store. All 8 were therefore cleared as
non-mutating and **all 8 were probed; none skipped**. No seed commands were configured.

## Discriminator handling

The `list` advisory flagged six candidates spanning the tools: `appName`, `appVersion`,
`baseUri`, `decimals`, `name`, `symbol`. None survives Pass 1 as a shape switch on
inspection — they are template *content*, not response selectors — but Pass 2 was run
anyway on `cairo-erc20` with three distinct argument sets (`name=MyToken`; `name=GovToken`
with `votes/appName/appVersion`; `name=Zed` with `decimals/mintable/burnable/upgradeable`).
All three returned `_observed_shape: "str"`, differing only in byte count (2285 / 3864 /
2828). Per the skill this is **inconclusive, not disproven** — recorded here as unconfirmed
— but it is moot: a bare string has no variant structure to model, so resolution is
option 3 (unwrap-only) for every tool regardless.

## Shape decisions

Every one of the 8 probes returned a plain `str` (1665–5936 bytes). A raw capture of
`cairo-erc20` via `mcpgen call` confirmed the payload is a fenced Cairo module
(```` ```cairo … #[starknet::contract] mod MyToken {…} ````), and the JSON-in-string guard
returned `NOT_JSON (JSONDecodeError)` — this is genuine source text, not a double-encoded
record. So for all 8 entries: `unwrap: []`, `return_model: null`, `fields: {}`,
`return_container` unset. `_observed_shape: "str"` is left in place as evidence of a real,
successful payload; no `_probe_status: inconclusive` marker was added, because every probe
did observe a success response. This server is a **`no_shaped_tool_by_design`** case — a
`TypedDict` here would be a fabricated claim about prose.

`probed_args` needed no scrubbing: every value is an invented placeholder (`MyToken`,
`MNFT`, `https://example.com/metadata/{id}.json`, `2026-03-15T14:30`), with no PII and no
machine-local paths.

## Regeneration

`mcpgen codegen` auto-detected the sidecar (`shapes: … (8 tool(s))`) and re-emitted the
module at 41470 bytes. It **parses cleanly** (`ast.parse` OK). All 8 wrappers stay `-> Any`,
which is the honest result. The schema work is still visible in the signatures: enum params
render as `Literal` without hand-widening — `type: Literal['stark','eth']`,
`schedule: Literal['linear','custom']`, `quorumMode: Literal['percent','absolute']`.
