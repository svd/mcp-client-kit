# deepwiki — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:27Z
- **Duration:** 3m 54s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list deepwiki --schema` returned **3 tools**, all probed, none skipped:

```
Tools on deepwiki:
  ask_question         — Ask any question about a GitHub repository and get an AI-powered, context-grounded response.
  read_wiki_contents   — View documentation about a GitHub repository.
  read_wiki_structure  — Get a list of documentation topics for a GitHub repository.
```

No tool carries an `annotations` block, so mutation classification fell through to the
keyword test plus a semantic read of each description. Splitting the names on `_` yields
`ask`/`question`, `read`/`wiki`/`contents`, `read`/`wiki`/`structure` — no whole word is on
the mutating list, and all three descriptions describe reads. Nothing was flagged, so no
`_mutating_suspect` markers were added and `mutating-skipped` is empty.

**discriminators: N/A.** `repoName` is the only parameter shared by more than one tool, and
it fails the advisory precondition twice over: `reponame` is one of the five identity forms
the engine denylists, and on `ask_question` it is expressed through `anyOf` rather than a
top-level scalar `"type"`. The `list --schema` stderr carried no advisory, confirming this.
Pass 2 was therefore skipped outright.

All three tools were probed against the public repo `modelcontextprotocol/servers` — a real,
well-documented target — with the ≥ 2 s hosted-endpoint pacing between calls.

## Surprises

Every probe came back `_observed_shape: "str"`. Because a text payload collapses to a bare
`"str"` and the words are lost, each tool's raw payload was captured with `mcpgen call --out`
and run through the JSON-in-string guard: all three returned `NOT_JSON` (`JSONDecodeError`).
Reading the payload heads confirmed they are genuine successes — a Markdown topic outline, a
Markdown prose answer, and a 373 KB Markdown wiki dump — not quota, auth, or 404 error text.
So `_observed_shape: "str"` is left as plain evidence of a real text-returning tool; no
`_probe_status: inconclusive` marker was warranted.

The size spread is the notable result: `read_wiki_structure` 1.2 KB, `ask_question` 2.0 KB,
`read_wiki_contents` 394 KB. Codegen picked the last one up from `_observed_bytes` and emitted
a payload-size warning into its docstring.

## Shape decisions

deepwiki has **no shapeable tool by design**. For all three: `unwrap: []` (no vendor envelope
exists — the MCP content block holds the Markdown directly), `return_model: null`, `fields: {}`,
`return_container` omitted. Setting a `TypedDict` on any of them would claim a dict the wrapper
never returns; `-> Any` over a `str` is the honest signature. No `input_overrides` were needed —
`ask_question`'s `anyOf` correctly renders `repoName: Any`, since the server genuinely accepts
either a string or a list of up to ten.

`probed_args` needed no scrubbing: a public repo slug and an authored question string are
functional values, not PII.

The regenerated module parsed cleanly (`ast.parse` OK).
