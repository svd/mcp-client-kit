# openzeppelin-solidity — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:46:06Z
- **Duration:** 3m 25s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

The server exposes **8 tools**, all of them contract generators named `solidity-<kind>`:
`solidity-erc20`, `solidity-erc721`, `solidity-erc1155`, `solidity-stablecoin`, `solidity-rwa`,
`solidity-account`, `solidity-governor`, `solidity-custom`. None carries an `annotations` block,
so classification fell back to the keyword-plus-semantic read. Every description ends with the
same sentence — *"Returns the source code of the generated contract, formatted in a Markdown
code block. Does not write to disk."* — which settles the verdict: the verbs (`Make`, `create
more supply`) are about the *generated* contract, not about server state. All 8 are pure
functions and all 8 were probed. Nothing was skipped as mutating, and no seed commands apply.

## Discriminators

`list --schema` raised seven candidates: `name` (all 8 tools), `symbol`, `decimals`,
`premint`, `premintChainId`, `namespacePrefix`, and `crossChainBridging`. Pass 1 disqualifies
none of them by name. Pass 2 probed `solidity-erc721` at `crossChainBridging="erc7786native"`
(869 bytes) and with the argument omitted (295 bytes) — the payload size changed but the shape
did not, both being a bare string. Recorded as **inconclusive, not disproven**; the other six
candidates are content substituted into the emitted source and cannot alter a response that has
no structure to vary. Resolution taken: option 3, unwrap-only, for all 8 tools.

## Shape decisions

Every probe returned a genuine success payload — verified by capturing three of them raw
(`solidity-erc721`, `solidity-custom`, `solidity-rwa`), each a fenced ```solidity block holding
real, compilable OpenZeppelin source. The payloads are prose, not double-encoded JSON, so the
JSON-in-string test reports `NOT_JSON` and `_observed_shape: "str"` stands as evidence rather
than as a probe failure. No `_probe_status: inconclusive` marker was written anywhere: nothing
was unobserved. Consequently `unwrap: []`, `return_model: null`, and `fields: {}` for all 8 —
this server is `no_shaped_tool_by_design`. `probed_args` needed no scrubbing; every value is an
invented literal (`EvalToken`, `EVT`, `https://example.com/token/{id}.json`).

## Findings

The regenerated module parses cleanly (`ast.parse` OK, 60950 bytes) with 8 `-> Any` wrappers and
no `_dig` helpers, which is correct here. One engine observation: `votes`, `access`, and
`upgradeable` declare their allowed values as `anyOf` unions of `const` strings rather than a
flat `enum` array, and codegen renders them `Any | None` instead of `Literal[...]`. Left
unedited on purpose — the artifacts must measure the skill, not a hand fix.
