# astro-docs — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-25T19:32:27Z
- **Duration:** 1m 17s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Surface

`astro-docs` is a single-tool HTTP server at `https://mcp.docs.astro.build/mcp`, no auth.
`mcpgen list --schema` returned exactly one tool, `search_astro_docs` ("Search the official
Astro framework docs"), taking one required string param `query`. No `annotations` block was
present, so the keyword + semantic fallback applied: `search` is a read verb with no mutating
language in the description, so the tool was probed. One tool probed, zero skipped, zero
mutating. With a single tool there were no shared params, so `mcpgen list` emitted no
discriminator advisory and no polymorphic-suspect set existed to resolve.

## Probing

Ran as a workflow subagent, so the whole procedure executed as one inline driver thread —
no recon or batch sub-subagents. A raw capture (`mcpgen call ... --out *.probe-raw.json`)
came back at 26.7 KB and showed the server returns a single-key vendor envelope:
`{"search_results": [...]}` with 10 hits. Nothing surprising: no error strings, no quota or
auth failures, no JSON-in-string wrapping (mcpgen surfaced a parsed dict, not text), and no
empty-list result that would have made the element shape unobservable.

Two live probes were merged in one `mcpgen probe` invocation (`"content collections"` and
`"view transitions"`) to confirm the element shape was stable across queries rather than an
artifact of one search. Both produced identical result records with four fields, all `str`
in both probes and never null: `content`, `source_url`, `title`, `source_type`. Deep-merge
widened nothing, which is itself the evidence that the shape is stable.

## Shape decisions

`search_astro_docs`: `unwrap: ["search_results"]` strips the envelope; `return_container:
"list"` because the unwrapped value is a list of records, so the body digs via `_dig_list`;
`return_model: "AstroDocSearchResult"` — a distinct, search-specific name, and the only
model in the module, so no collision check was needed. All four top-level scalars were
promoted; there is no nesting to over-model, and `content` stays a `str` (raw Markdown), not
a parsed structure. `probed_args` are plain public search strings — no PII pattern matched,
so nothing was scrubbed and the roundtrip verifier keeps working against the live server.

## Verification

Regenerated with `--shapes`; `ast.parse` succeeded and `search_astro_docs` reads
`-> list[AstroDocSearchResult]`, not `Any`. Runner generation was skipped per the subagent
fallback for step 7.
