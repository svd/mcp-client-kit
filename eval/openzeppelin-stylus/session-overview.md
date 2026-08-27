# openzeppelin-stylus — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:48:56Z
- **Duration:** 3m 7s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

The server exposes **3 tools**, all contract generators: `stylus-erc20`, `stylus-erc721`,
`stylus-erc1155`. No tool carries `annotations`, so classification fell back to the keyword
test plus a semantic read of the description. The verb "Make" reads as mutating, but every
description ends with "Does not write to disk" — the tools return source code and touch no
persistent state. All three were cleared as safe-to-probe and **all three were probed**;
nothing was skipped. No seed commands apply.

## Discriminator handling

`list --schema` raised one candidate: **`name` → stylus-erc1155, stylus-erc20, stylus-erc721**.
`name` is not on the engine denylist (only `reponame`/`repo_name`/`repositoryname`/`username`/
`orgname` are) and is not one of the Pass 1 pagination/sort/path additions, so it survived
Pass 1 and required Pass 2. Three distinct values were probed against `stylus-erc20` in
separate paced invocations, reading the part file between each: `MyToken` (2557 B), `Zeta`
(2539 B), `Q3_Rewards` (2575 B). All three returned the identical shape `"str"`; byte counts
differ only because the contract name is interpolated into the emitted Rust. That is
**inconclusive, not disproven** — but it is moot here, because there is no structured model
for the parameter to switch between. Resolved as **option 3 (unwrap-only)**.

## Shape decisions

Every tool returned `_observed_shape: "str"`. The JSON-in-string test was run on a captured
raw payload for `stylus-erc20` and came back **`NOT_JSON`**: the body is a Markdown fenced
block of Rust (`` ```rust `` / `// SPDX-License-Identifier: MIT` / `use openzeppelin_stylus::…`),
not a double-encoded record. That is an expected outcome, not a probe failure — each probe
returned a real success payload, so no `_probe_status: inconclusive` marker was added.

Consequently all three entries keep `unwrap: []`, `return_model: null`, empty `fields`, and
`source: "live"`. No `input_overrides` were needed; the input schemas are honest (required
`name: string`, optional booleans, a nested `info` object). This server is
**`no_shaped_tool_by_design`** — prose/source in, prose/source out, with no vendor envelope to
strip. `probed_args` hold only invented contract labels, so the scrub pass had nothing to
remove.

## Generation

Codegen re-consumed the merged shapes (`shapes: … (3 tool(s))`) and correctly emitted all
three wrappers as `-> Any` rather than inventing a `TypedDict`. The module parses cleanly
(`ast.parse` OK, 6103 bytes) with `__schema__` and Args docstrings embedded on each function.
Runner generation was left to the harness verify stage per the eval contract.
