# openzeppelin-stellar — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:07:34Z
- **Duration:** 2m 54s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **6 tools**, all Soroban contract-source generators:
`stellar-account`, `stellar-fungible`, `stellar-governor`, `stellar-non-fungible`,
`stellar-stablecoin`, `stellar-vault`. None carries an `annotations` block, so the
step-2b fallback applied: the keyword test matches no name (`stellar-*` plus a contract
kind), and every description ends with the same sentence — "Returns the source code of
the generated contract, formatted in a Markdown code block. **Does not write to disk.**"
That is an explicit server-side statement of read-only behaviour, so all 6 were selected
and probed; **0 skipped**, no `mutating-skipped` entries.

## Discriminators

`mcpgen list` raised four candidates: `decimals` (2 tools), `name` (6), `premint` (2),
`symbol` (4). All survive Pass 1 — none is on the engine denylist or the camelCase
pagination/path additions. Pass 2 probed `stellar-fungible` at three distinct value sets
(`symbol=ACME`; `symbol=BETA, decimals=18, premint=1000`; `symbol=GMA, decimals=2` with
five feature flags on). All three observed `str` — identical shape, differing only in
byte count (616 / 697 / 2967), which is content, not shape. **Verdict: inconclusive, not
disproven.** It cannot be otherwise here: the response is a flat string in every case, so
no argument can switch its structure. Because `return_model` stays `null` throughout, the
inconclusive verdict costs nothing — nothing is typed more precisely than the probes
justify.

## Probe results and shape decisions

Every one of the 6 tools returned `_observed_shape: "str"` from a live call, at 608–2967
bytes. Raw payloads were captured for `stellar-fungible` and `stellar-account` and both
are genuine Rust source inside a ` ```rust ` fence — real success payloads, not error
strings, so no `_probe_status: inconclusive` marker applies. The JSON-in-string test ran
against both captures and returned `NOT_JSON` (`JSONDecodeError`): the payload is prose,
not a double-encoded record.

Consequently, for all six entries: `unwrap: []`, `return_model: null`,
`return_container` omitted, `fields: {}`, `source: "live"`. There is no vendor envelope
to strip and no record to model — inventing an unwrap path would make `_dig` return a
substring of source code. `_observed_shape` was kept as evidence, per the harness rule.
This is the `no_shaped_tool_by_design` case: a shaped return here would be a lie.

`probed_args` needed no scrubbing — every value is an invented contract name or symbol
(`AcmeToken`, `AUSD`, `AcmeVault`), functional rather than personal.

## Regeneration

`codegen` re-ran with the shapes file auto-detected (6 tools). The module parses cleanly
under `ast.parse`; all six wrappers read `-> Any`, which is the honest signature. No
`TypedDict` classes were emitted. No `Literal[...]` params appear either: `access` and
`policy` express their choices through `anyOf`/`const` rather than a top-level `enum`
array, so codegen renders `access: Any | None` — correct, if less precise than the
schema allows.
