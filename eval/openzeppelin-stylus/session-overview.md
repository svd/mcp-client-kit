# openzeppelin-stylus — session overview

## Run Metadata

- **Executed:** 2026-08-27T11:12:28Z
- **Duration:** 1m 58s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

`mcpgen list openzeppelin-stylus --schema` reported **3 tools**, all contract-scaffold
generators for Arbitrum Stylus: `stylus-erc20`, `stylus-erc721`, `stylus-erc1155`. All three
were probed; none was skipped. No seed commands were required (`auth: none`, hosted HTTP).

## Mutating classification

No tool carries `annotations`, so classification fell back to the keyword-plus-semantic read.
Each description opens with "Make a …", which trips the keyword test, but each also states
explicitly: *"Returns the source code of the generated contract, formatted in a Markdown code
block. **Does not write to disk.**"* These are pure code generators with no server-side state,
so all three were cleared as safe to probe. Nothing was flagged `_mutating_suspect`.

## Discriminator handling

The `list --schema` advisory flagged `name` as a discriminator candidate spanning all three
tools. `name` survives Pass 1 (it is not one of the five identity forms the engine drops), so
Pass 2 ran: three separate paced probes of `stylus-erc20` with `AlphaToken`, `BetaCoin`, and
`GammaAsset`, reading the part file between each. All three returned `_observed_shape: "str"`,
differing only in `_observed_bytes` (2575 / 2563 / 2575) — byte counts, not shape. **Verdict:
inconclusive, not disproven**, so the three tools formally stay polymorphic-suspect. A
description sweep found no second candidate; `name` is documented as "The name of the contract",
free text with no `enum`, and it steers the emitted identifier rather than the response shape.

Resolution: **option 3 (unwrap-only / `Any`)**. Since every response is a flat string, there is
no dict to key overloads on and no shared base model to extract — the honest outcome is the same
one a confirmed non-discriminator would produce.

## Shape decisions

All three tools returned prose. A raw capture via `mcpgen call --out` confirmed the payload is a
literal Markdown fence containing Rust source (` ```rust ` / `// SPDX-License-Identifier: MIT`),
i.e. **NOT_JSON** — not a double-encoded record, so no JSON-in-string unwrap applies. Each entry
therefore keeps `unwrap: []`, `return_model: null`, `fields: {}`, `source: "live"`, and
`_observed_shape: "str"` as honest evidence of a genuine text return. No `_probe_status:
inconclusive` marker was added: every probe returned a real success payload.

This is a `no_shaped_tool_by_design` server — a typed `TypedDict` would misdescribe what every
tool actually hands back.

## Verification

Regeneration wrote a 6103-byte module that `ast.parse` accepts cleanly. Signatures are fully
typed from `inputSchema` (`name: str` required; `burnable`/`permit`/`flashmint`/`enumerable`/
`supply` as `bool | None`; `info` as a nested dict) and all three return `Any`, which matches the
observed shapes.
