# openzeppelin-stellar — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:48:37Z
- **Duration:** 2m 53s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **6 tools**, all Soroban contract generators: `stellar-account`,
`stellar-fungible`, `stellar-governor`, `stellar-stablecoin`, `stellar-non-fungible`,
`stellar-vault`. Every description ends "Returns the source code of the generated contract,
formatted in a Markdown code block. Does not write to disk." None carries `annotations`, but
the semantic read is unambiguous: nothing is persisted server-side, so all 6 cleared the
mutating check and all 6 were probed. Nothing was skipped. No seed commands were configured.

## Discriminators

`mcpgen list` flagged four candidates — `name` (6 tools), `symbol` (4), `decimals` (2),
`premint` (2). All four survived Pass 1: none sits on the engine denylist and each declares a
top-level `"type": "string"`. Pass 2 probed `stellar-fungible` at three values
(`symbol=AAA`, `symbol=BBBBBB`, `symbol=XYZ` with `decimals=18` and `premint=1000`) and got an
identical bare `"str"` every time — 619, 622 and 700 bytes. Per the skill that is
**inconclusive, not disproven**, so all six tools stay polymorphic-suspect and were resolved
under option 3 (unwrap-only, no model). That is the only defensible option here: a payload with
no keys has nothing for a discriminator to switch.

## Interesting responses

Every probe returned `_observed_shape: "str"`. Because a bare `str` can also mean a
double-encoded record, the raw payload was captured with `mcpgen call --out` and tested: it
came back **NOT_JSON** — the body is literally a ```rust fence wrapping generated Soroban
source. So the `str` is genuine prose, not an envelope. These are successful results, not
errors, so no `_probe_status: inconclusive` marker was written; the honest record is the plain
`_observed_shape`.

One codegen note: the `access`, `policy` and `upgradeable` params express their allowed values
as `anyOf` branches of `{"type": "string", "const": ...}` rather than a flat `enum`, so codegen
emitted no `Literal[...]`. That is the schema's shape, not a codegen miss.

## Shape decisions

Identical for all six tools: `unwrap: []`, `return_model: null`, `return_container: null`,
`fields: {}`. There is no key path to dig, and inventing one would make `_dig` return a
substring instead of the source. Each entry carries a `_note` and a `_discriminator_status`
recording the Pass 2 verdict.

## Verification

The regenerated module parses cleanly under `ast.parse`, and all six wrappers correctly read
`-> Any`. This server is a true `no_shaped_tool_by_design` case, not a coverage gap. `run.py`
already existed in the output folder and was left untouched.
