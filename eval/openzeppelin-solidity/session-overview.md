# openzeppelin-solidity — skill run overview

## Run Metadata

- **Executed:** 2026-08-27T11:10:28Z
- **Duration:** 3m 51s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

`mcpgen list --schema` returned **8 tools**, all Solidity contract wizards:
`solidity-erc20`, `solidity-erc721`, `solidity-erc1155`, `solidity-stablecoin`,
`solidity-rwa`, `solidity-account`, `solidity-governor`, `solidity-custom`.
No tool carries `annotations`, so classification fell back to the keyword plus
semantic read. Every description ends "Returns the source code of the generated
contract, formatted in a Markdown code block. **Does not write to disk.**" — the
`make`/`create` verb is about contract *text*, not server state, so all 8 cleared
as non-mutating. **All 8 were probed; none skipped.** No seed commands apply.

## Discriminator handling

The `list` advisory flagged seven candidates: `crossChainBridging`, `decimals`,
`name`, `namespacePrefix`, `premint`, `premintChainId`, `symbol`. Pass 1 dropped
none by name. Pass 2 probed `solidity-custom` at three distinct `name` values
(`MyContractAlpha`, `BetaVault` + `pausable`, `GammaToken` + `upgradeable=uups`):
all three returned the identical shape `"str"`, differing only in byte count.
The advisory's candidates are contract *configuration* knobs — they change the
emitted Solidity text, never the response type — so all seven resolve to
option 3 (no model) rather than variants.

## Surprises

- `solidity-rwa` and `solidity-stablecoin` first failed with
  `MCP error -32602 … expected string, received number` on `decimals`. That was
  my probe args, not a schema lie: both declare `decimals` as `"type": "string"`.
  Notably `solidity-governor` declares the same-named param as `"number"`, which
  is why the name-matched advisory grouped four tools whose types disagree.
  Re-probed with `"18"` / `"6"` and both returned real contract source.
- Payload sizes span 423 B (`solidity-rwa`, minimal options) to 3298 B
  (`solidity-governor`) — small responses here are genuine output, not errors.
- A guarded `json.loads` on the raw `solidity-rwa` payload returned `NOT_JSON`:
  the body is a literal ```` ```solidity ```` fenced block, not double-encoded JSON.

## Shape decisions

Identical for all 8 tools: `unwrap: []`, `return_model: null`, `fields: {}`,
`_observed_shape: "str"`. There is no vendor envelope and no record — the
response *is* prose. Inventing an unwrap path would make `_dig` return a
fragment the wrapper never produced, so every wrapper honestly stays `-> Any`.
This is `no_shaped_tool_by_design`, not a coverage gap.

The regenerated module parses cleanly (`ast.parse` OK, 60950 bytes, 8 typed
`async def`s). `probed_args` hold only synthetic contract names — no PII.
