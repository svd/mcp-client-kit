# openzeppelin-solidity — session overview

## Run Metadata

- **Executed:** 2026-08-25T19:32:27Z
- **Duration:** 2m 20s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Surface

The server exposes **8 tools**, all contract generators for the OpenZeppelin Solidity
Wizard: `solidity-erc20`, `solidity-erc721`, `solidity-erc1155`, `solidity-stablecoin`,
`solidity-rwa`, `solidity-account`, `solidity-governor`, `solidity-custom`. None carries
`annotations`, so the keyword + semantic fallback decided safety. Every description ends
with "Does not write to disk", which settles it: these are pure functions from an options
object to source text. **All 8 were probed, none skipped.**

## Probe results

Every tool returned the same thing — a plain `str` containing a Markdown-fenced Solidity
file. Payloads ranged from 111 bytes (`solidity-custom`, an empty contract body) to
3298 bytes (`solidity-governor`, which pulls in the full Governor stack). Raw payloads were
captured for `solidity-custom` and `solidity-erc20` to rule out the JSON-in-string case:
both are literal ```` ```solidity ```` blocks, not serialized structures, so `json.loads()`
would fail and `_observed_shape: "str"` stands as the honest answer. These are genuine
successes, not quota or auth errors — no `_probe_status: inconclusive` was warranted.

## Shape decisions

**No tool received a `return_model`.** There is no vendor envelope to strip
(`unwrap: []` everywhere) and no record to model — the payload is the contract source
itself. Minting a `TypedDict` here would be a lie about a string. All 8 wrappers correctly
stay `-> Any`, and `fields` is empty for each.

The one substantive edit was an `input_override`: `solidity-governor.decimals` is declared
`number` in the input schema but is a token decimal count (18 for ERC20Votes, 0 for
ERC721Votes), so it was pinned to `int` rather than the mechanical `float`. `blockTime` and
`quorumPercent` were left `float` — both plausibly accept fractional values.

The `mcpgen list` advisory flagged seven discriminator candidates (`name`, `symbol`,
`decimals`, `premint`, `premintChainId`, `namespacePrefix`, `crossChainBridging`). All were
discarded. `name` spans every tool in the set — a global context arg, Pass-1 disqualified.
The rest failed Pass 2: no response is a dict, so none of these can appear as a response
key. They are contract-configuration inputs that steer which Solidity features get emitted,
not shape switches over a structured payload.

## Outcome

The regenerated module parses cleanly (`ast.parse` OK) with all 8 `async def` wrappers
present. `eval-kit verify` returns **pass**: ast, signatures, idempotency, and pii all
green; roundtrip skipped as `no_shaped_non_mutating_tool`, the expected result when nothing
carries a return model.
