# astro-docs — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:45:57Z
- **Duration:** 2m 7s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

`astro-docs` is a hosted HTTP MCP endpoint (`https://mcp.docs.astro.build/mcp`, no auth) that
exposes exactly **one tool**: `search_astro_docs` — "Search the official Astro framework docs".
Its `inputSchema` declares a single required `query: string` with `additionalProperties: false`.
No `annotations` block is supplied, so the read-only verdict came from the fallback path:
the name and description are unambiguously a search, there is no mutating verb anywhere in the
surface, and the schema has no field capable of writing state. Tools probed: 1 of 1; skipped: 0.
No seed commands were configured, and none were needed — the docs corpus is server-side and
always populated.

**Discriminators: N/A.** The candidate advisory on `list --schema` was silent, and it could not
have fired: a candidate needs two or more tools sharing a scalar parameter name, and this server
has one tool with one parameter, which is `query` — itself on the engine's denylist.

## Probing

Two paced probes were issued against the hosted endpoint, the second as a multi-`--args`
invocation so the two responses deep-merged into a single observed shape: `"content collections"`
and `"view transitions astro:page-load"`. Both returned the same envelope, with no key present in
one and absent from the other and no type widening:

```
{"search_results": [{"content": str, "source_url": str, "title": str, "source_type": str}, "...x10"]}
```

Nothing surprising surfaced. There was no double-encoding (the payload arrives as a real object,
not a JSON string), no empty list, no error envelope, and no null in any field across 20 observed
records. Responses are sizeable — ~27 KB for ten hits, since `content` carries full doc excerpts —
which is exactly the "big dump" profile the shape-spec is meant to keep out of model context.

## Shape decisions

- `search_astro_docs` → **unwrap `["search_results"]`**, `return_container: "list"`,
  `return_model: AstroDocSearchResult`. The envelope has a single key wrapping the record list,
  so the unwrap path is unambiguous and the container is a list rather than a dict.
  All four fields were promoted as plain `str`: each was observed on every record across both
  queries, all are top-level scalars, and none was ever null. Nothing deeper was modelled — the
  records are flat, so there is no second level to under-describe. `source: live`.
- `probed_args` needed no scrubbing: both values are public free-text search queries with no
  identifier, path, or PII component.

## Verification

The regenerated module parses cleanly (`ast.parse` OK) and the eval target holds:
`search_astro_docs` reads `-> list[AstroDocSearchResult]`, not `Any`, and its body digs the
envelope through `_dig_list(result, ('search_results',))`. `run.py` was left to the harness's
verify stage, as instructed.
