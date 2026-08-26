# openzeppelin-stellar — session overview

## Run Metadata

- **Executed:** 2026-08-26T14:48:03Z
- **Duration:** 1m 49s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

The server exposes **6 tools**, all contract generators: `stellar-account`,
`stellar-fungible`, `stellar-governor`, `stellar-non-fungible`, `stellar-stablecoin`,
`stellar-vault`. Every one is read-only by construction — each description ends with
"Returns the source code of the generated contract, formatted in a Markdown code block.
Does not write to disk." No `annotations` block is published, so the keyword+semantic
fallback applied; nothing tripped it. **All 6 were probed, 0 skipped.** No seed commands
were configured, and none were needed — the server holds no state.

## Discriminator handling

`mcpgen list` flagged four candidates: `name` (spans all 6 tools), `symbol` (4 tools),
`decimals` and `premint` (fungible + stablecoin). All four were disqualified at Pass 2:
every probed response is a bare `str`, so none of these parameters can appear as a key
in an observed response dict. They are contract-configuration inputs, not response shape
switches. No `discriminator`/`variants` blocks were emitted, and no single-variant model
was minted.

## Probe results and shape decisions

Every probe succeeded on the first call with minimal valid args (`name`, plus `symbol`
where required). Responses ranged from 607 to 2183 bytes. Each one is a Markdown-fenced
Rust source file — for example `stellar-vault` returned a `#![no_std]` Soroban contract
importing `stellar_tokens::vault::{FungibleVault, Vault}`. `json.loads()` on the payload
fails, so the JSON-in-string rule does not apply and `_observed_shape: "str"` stands as
the honest answer.

The shape decision is therefore identical for all six tools: `unwrap: []`,
`return_model: null`, `fields: {}`, `return_container` omitted, `source: "live"`. There
is no vendor envelope to strip and no record to model — the payload *is* the string.
Minting a `TypedDict` here would be an authoritative lie about a scalar. This is the
`no_shaped_tool_by_design` case, matching the sibling `openzeppelin-solidity` run.

One codegen observation worth recording: the `access`, `policy`, and `limitations`
parameters declare their allowed values as `anyOf` arrays of `const` schemas rather than
a flat `enum`, so `py_type()` widens them to `Any | None` instead of narrowing to
`Literal['ownable', 'roles']`. That is a schema-shape gap on the server side, not a
wrapper defect — the values are still documented in each function's Args docstring via
`--embed-schema`.

## Verification

The regenerated module (29114 bytes) parses cleanly under `ast.parse`. All six
signatures read `-> Any`, which is the correct and expected outcome for a server whose
entire surface returns prose. `probed_args` needed no scrubbing: every value is a
synthetic contract name (`MyToken`, `MVLT`) with no PII, and they must stay verbatim so
the roundtrip verifier can replay a real call.
