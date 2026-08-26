# openzeppelin-cairo — Session Overview

## Run Metadata

- **Executed:** 2026-08-26T14:48:02Z
- **Duration:** 2m 8s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and coverage

The server exposes **8 tools**, all probed, none skipped. Each is a contract-source
generator: `cairo-erc20`, `cairo-erc721`, `cairo-erc1155`, `cairo-account`,
`cairo-multisig`, `cairo-governor`, `cairo-vesting`, `cairo-custom`. No seed commands
were configured, and none were needed — the server is stateless.

The keyword heuristic flags every one of these as mutating ("Make a …"), but the
descriptions settle it explicitly: *"Returns the source code of the generated contract,
formatted in a Markdown code block. Does not write to disk."* No `annotations` block is
present on any tool, so the semantic read decided it: these are pure functions of their
arguments. All 8 were probed live.

## Discriminators

`mcpgen list` raised six discriminator candidates — `name` (all 8 tools), `symbol`,
`decimals`, `appName`, `appVersion`, `baseUri`. Every one is disqualified. `name` spans
the entire selected set, which is the global-context-arg pattern; the rest are contract
construction parameters (token symbol, decimal count, EIP-712 domain) that get baked into
the emitted source and cannot appear as response keys, because the response has no keys
at all. `cairo-account` also raised a real enum discriminator on `type`; I probed both
`stark` and `eth` to close it rather than typing from one variant. Both returned the same
shape (`str`), so the warning resolves with no variant model.

## Shape decisions

Identical for all 8 tools: `unwrap: []`, `return_model: null`, `fields: {}`. The observed
shape is `str` in every case, between 1.6 KB and 5.9 KB of payload.

This is a genuine prose surface, not a probe failure and not a masked envelope. I pulled
one raw payload (`cairo-erc20`) to check: it is a Markdown-fenced Cairo module
(` ```cairo ` … `#[starknet::contract] mod MyToken { … }`), a real success response with no
vendor wrapper to strip. It is not JSON-in-string — `json.loads()` rejects it at char 0 —
so the JSON-unwrap path does not apply. No quota, rate-limit, or auth error appeared on
any probe, so no `_probe_status: inconclusive` markers were needed.

Fabricating a `TypedDict` here would be the exact over-modelling the skill warns against.
Every tool honestly stays `-> Any`, with `_observed_shape` retained as evidence.

No scrubbing was required: probed args are synthetic contract names (`MyToken`, `MTK`),
`example.com` URIs, and functional enum values — none PII.

## Generated module

`ast.parse` succeeds. Eight `async def` wrappers, all `-> Any`, zero `TypedDict` classes.
Enum params rendered as `Literal[...]` automatically — `type: Literal['stark', 'eth']`,
`schedule: Literal['linear', 'custom']`, `votes: Literal['erc20votes', 'erc721votes']` —
so the input side is meaningfully typed even though the return side cannot be.
