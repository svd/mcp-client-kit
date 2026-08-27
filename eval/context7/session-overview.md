# context7 — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:30:40Z
- **Duration:** 8m 55s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

`mcpgen list context7 --schema` reported **2 tools**: `resolve-library-id` and `query-docs`.
Both carry `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, so step 2b
cleared both on the annotation path alone. No mutating tools exist here, so nothing was skipped:
**2 of 2 tools probed**. No seed commands were configured or needed.

## Discriminators

`discriminators: N/A`. The two tools share exactly one parameter name, `query`, and `query` sits
on the engine's own lowercased-exact-name denylist, so it can never be advertised as a candidate.
The remaining parameters (`libraryName`, `libraryId`) each appear on a single tool and so fail the
two-or-more-tools precondition. The `list --schema` stderr carried no advisory, consistent with
the precondition. Pass 2 was therefore skipped outright — no live variant probes were spent.

## Probes and responses

Both probes returned real, non-empty success payloads on the first attempt:

- `resolve-library-id` with `{"libraryName": "Next.js", "query": "How to configure middleware"}`
  → 2078 bytes.
- `query-docs` with `{"libraryId": "/vercel/next.js", "query": "How to configure middleware in
  Next.js"}` → 5396 bytes. The library id was bootstrapped from the `resolve-library-id` raw
  capture rather than invented, so the call hit a library that genuinely exists.

The surprise, such as it is, is that **neither tool returns structured data.** Both
`_observed_shape` values came back as `"str"`. `resolve-library-id` emits a human-readable
"Available Libraries:" block — `- Title:` / `- Context7-compatible library ID:` lines separated
by `----------` rules — that resembles a record list but is one flat string with no envelope. `query-docs` returns Markdown documentation with
fenced code blocks. Per the JSON-in-string check, both raw captures were re-tested with a guarded
`json.loads()`; both reported `NOT_JSON (JSONDecodeError)`, confirming the payloads are prose and
not double-encoded JSON. That is an expected outcome, not a probe failure, so no
`_probe_status: "inconclusive"` marker was recorded — the shapes were observed, and what was
observed is text.

## Shape decisions

- **`resolve-library-id`** — `unwrap: []`, `return_model: null`, `fields: {}`. There is no key
  path to dig: the payload *is* the string. Inventing an unwrap path here would make `_dig`
  return a field the wrapper never receives.
- **`query-docs`** — identical reasoning; Markdown prose, `unwrap: []`, `return_model: null`.

Parsing the `- Title:` blocks into a `TypedDict` would mean writing a text parser, not recording
an observed shape, so both wrappers stay `-> Any` honestly. `probed_args` needed no scrubbing —
a public library name and two generic technical queries, no PII, no machine-local paths.

## Generated module

The regenerated `eval/context7/context7.py` (7152 bytes) parses cleanly under `ast.parse`. Final
signatures: `query_docs(caller, *, libraryId: str, query: str) -> Any` and
`resolve_library_id(caller, *, query: str, libraryName: str) -> Any`. `--embed-schema` attached `__schema__` plus Args
docstrings to both functions. This server is the `no_shaped_tool_by_design` case: the
correct output is two unshaped wrappers, and that is what the skill produced.
