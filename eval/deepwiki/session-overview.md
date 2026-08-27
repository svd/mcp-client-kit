# deepwiki — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:39Z
- **Duration:** 8m 38s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list deepwiki --schema` reported **3 tools**: `ask_question`, `read_wiki_contents`,
`read_wiki_structure`. The server publishes no `annotations` block, so classification fell back
to the keyword plus semantic read: all three are pure reads over a public documentation index —
`ask`/`read` verbs, no create/update/delete surface — so **all 3 were selected and probed, 0
skipped**. No seed commands were configured, and none were needed: DeepWiki's store is the
public GitHub corpus, already populated.

**Discriminators: N/A.** All three tools share `repoName`, but the engine's Pass-1 denylist drops
`reponame` on a lowercased exact match, so no advisory fired on the `list --schema` stderr and no
candidate cleared the precondition. Nothing was left polymorphic-suspect.

Probe argument for every tool was the real public repo `facebook/react`; `ask_question` also took
the free-text question "What is the fiber reconciler?". Probes were issued as separate, paced
invocations because this is a hosted HTTP endpoint.

## Responses

Every tool returned a bare `str`, at three very different sizes: 1,441 bytes for
`read_wiki_structure`, 2,883 for `ask_question`, and **635,591** for `read_wiki_contents` — the
full rendered wiki for one repository in a single response, easily the most notable observation of
the run.

Because `_observed_shape == "str"` is also the signature of a double-encoded payload, each tool's
raw response was captured with `mcpgen call --out` and JSON-tested. All three came back
`NOT_JSON`: `read_wiki_structure` is a plain indented topic outline, `read_wiki_contents` is
Markdown with `# Page:` headers and `<details>` blocks, and `ask_question` is an AI-authored prose
answer with inline citations. There is no vendor envelope anywhere on this server — nothing to
unwrap, and no record to promote.

## Shape decisions

Identical for all three tools: `unwrap` empty, `return_model` `null`, `fields` empty,
`source: "live"`. Minting a `TypedDict` here would state an authoritative lie about a payload that
is genuinely free text. `_observed_shape: "str"` is retained as evidence, and deliberately *not*
recorded as `_probe_status: "inconclusive"` — every probe returned a real success payload, so the
`str` is an observed fact, not an unobserved shape.

One honest `Any` remains: `ask_question`'s `repoName` is declared `anyOf: [string, array<string>]`,
so codegen types it `Any`. No `input_override` was added — the schema is not lying, it really does
accept both, and narrowing it would be invention rather than a correction.

## Result

The regenerated module parses cleanly (`ast.parse` OK) and exposes all 3 async wrappers, each
returning `Any` by design. This server is a `no_shaped_tool_by_design` case: zero shaped tools is
the correct outcome, not a coverage gap.
