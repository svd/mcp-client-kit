# astro-docs — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:10:10Z
- **Duration:** 2m 19s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Environment

`mcpgen` resolved to `uv run mcpgen` (0.9.0.dev1), above the 0.7.0 floor. The server is a
hosted streamable-HTTP endpoint (`https://mcp.docs.astro.build/mcp`) with no auth, reached
through `MCPGEN_SERVERS=.mcp.eval.json` on every live command.

## Tool inventory

`mcpgen list astro-docs --schema` reports exactly **one** tool: `search_astro_docs`
(`query: string`, required, `additionalProperties: false`). The server supplies no
`annotations` block, so the mutating classification fell to the keyword plus semantic read:
`search` is a pure read verb over a public documentation corpus with no write-shaped
parameters. Selected set: 1 of 1 tool. Nothing was skipped, and no mutating tool exists to
skip.

**Discriminators: N/A.** The advisory cannot fire on a single-tool server — no parameter is
shared by two or more tools — and the description sweep found nothing: `query` is free text
and the description names no response key that varies by argument. Pass 2 was therefore
skipped outright.

## Surprising response

The very first `list --schema` invocation returned the **two Cloudflare docs tools**
(`search_cloudflare_documentation`, `migrate_pages_to_workers_guide`) instead of the Astro
tool, while the `codegen` run immediately before it correctly emitted `search_astro_docs`. An
identical re-run of the same command returned the correct single Astro tool. Both servers are
hosted HTTP endpoints declared in the same `.mcp.eval.json`; this looks like cross-server
bleed in a cached HTTP session rather than anything about astro-docs itself. Recorded here
because it is reproducible only intermittently and would silently type a wrapper against the
wrong server.

## Shape decisions

Two probes were issued (`"content collections"`, `"view transitions astro:page-load"`),
paced ≥2 s apart against the hosted endpoint. Both returned an identical envelope:

```
{"search_results": [{"content", "source_url", "title", "source_type"}, ...x10]}
```

- **unwrap:** `["search_results"]` — a single-key vendor envelope wrapping the real records.
- **return_container:** `list` — the unwrapped value is a list of chunks, so the wrapper
  returns `list[SearchDocItem]` and digs via `_dig_list`.
- **return_model:** `SearchDocItem` — a search endpoint returning per-chunk items, named per
  the search-endpoint convention rather than reusing a document noun.
- **fields:** all four top-level scalars are `str` and were present in every element across
  both probes, so none is marked nullable. Nothing below the top level was modelled — the
  elements are flat, so there was no depth to over-promote.

`probed_args` are free-text search queries carrying no PII; nothing needed scrubbing.

## Verification

Regeneration with the shape-spec in place parsed cleanly under `ast.parse`, and the shaped
signature reads `search_astro_docs(...) -> list[SearchDocItem]` with a `_dig_list` body over
`('search_results',)` — no residual `Any`.
