# openzeppelin-stylus — session overview

## Run Metadata

- **Executed:** 2026-08-26T14:48:03Z
- **Duration:** 1m 23s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list` returned **3 tools**, all contract-scaffolding generators:
`stylus-erc20`, `stylus-erc721`, `stylus-erc1155`. None carry `annotations`,
so mutation screening fell back to the keyword + semantic read. The verb
"Make" is not on the keyword list, and every description ends with
"Returns the source code of the generated contract, formatted in a Markdown
code block. **Does not write to disk.**" — an explicit non-mutation statement.
All 3 were treated as read-only and probed; **0 skipped**. No seed commands
apply (the server holds no store).

## Discriminator handling

`mcpgen list` raised one discriminator candidate: `name`, spanning all three
tools. It survived Pass 1 (not a pagination/sort/path name) but failed Pass 2:
`name` is the contract identifier written *into* the emitted Rust source and
never appears as a key in any observed response — the responses are not dicts
at all. Discarded as input-only. No variant probing was performed, and no
`discriminator`/`variants` block was written.

## Probe results

All three probes ran with the single required arg `{"name": "MyToken"}` and
succeeded live (2.0–2.6 KB each). Every response is a plain string: a fenced
```rust code block containing a complete OpenZeppelin-for-Stylus contract —
SPDX header, `#![cfg_attr(...)] no_main`, `use openzeppelin_stylus::…`
imports, and `#[public] impl` blocks. Payload size tracks feature surface
(erc20 2557 B > erc721 2378 B > erc1155 2041 B), consistent with the erc20
probe defaulting to the `permit`/`Nonces` extension.

A raw `mcpgen call` capture confirmed the string is genuine source text, not
an error envelope and not JSON-in-string: `json.loads()` fails on the first
character (a backtick). No quota, auth, or rate-limit signals appeared, so no
`_probe_status: "inconclusive"` marker was needed.

## Shape decisions

Identical for all three tools: `unwrap: []`, `return_model: null`,
`return_container` omitted, `fields: {}`, `source: "live"`,
`_observed_shape: "str"` retained as evidence. There is no vendor envelope to
strip and no record to model — the payload *is* prose. Minting a `TypedDict`
here would be an authoritative lie about a string, so the wrappers correctly
stay `-> Any`. This is the `no_shaped_tool_by_design` case: **0 shaped tools
out of 3, by design, not by coverage gap.**

`probed_args` needed no scrubbing — `"MyToken"` is an invented functional
value, not PII.

## Verification

Regeneration with `--shapes` produced a byte-identical 6103-byte module
(shapes carry no models, so codegen output is unchanged from the mechanical
pass). `ast.parse` succeeded; all three `async def`s render their optional
booleans as `bool | None = None` and the nested `info` object as `dict | None`.
