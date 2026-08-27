# context7 — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T05:57:28Z
- **Duration:** 2m 15s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

`mcpgen list context7 --schema` reported **2 tools**, and both were probed; none were
skipped. Both carry a complete, self-consistent `annotations` block —
`readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true` — so step 2b's
primary path cleared them outright. Neither name trips the keyword test, so nothing landed in
`mutating-skipped`. Resolved CLI: `uv run mcpgen` (0.9.0.dev1). Seeds: none.

**discriminators: N/A.** No advisory fired on the `list --schema` stderr, and the
precondition explains why: the only parameter the two tools share is `query`, which is on
the engine's own denylist. `libraryName` and `libraryId` each appear on exactly one tool,
so no candidate can clear the two-tool test. Step 2.e Pass 2 was skipped as a result, and
no tool is polymorphic-suspect.

## Probes and what came back

`resolve-library-id` was probed with `{libraryName: "Next.js", query: "How to configure
middleware"}` and `query-docs` with `{libraryId: "/vercel/next.js", query: "How to
configure middleware in Next.js"}`. The `libraryId` is not invented — it is a documented
example in the tool's own schema description, and `resolve-library-id`'s payload
independently confirmed it. Both probes succeeded first try.

The notable result is that **both tools observed as bare `"str"`** (2 097 and 5 396
bytes). Because a text payload collapses to `"str"` and loses its words, both raw
payloads were captured with `call --out` before anything was classified. The guarded
JSON-in-string test returned `NOT_JSON` for both — a `JSONDecodeError`, not a parse of a
double-encoded record. Reading the captured payloads confirmed why: `resolve-library-id`
returns a human-readable `Available Libraries:` listing with `- Title: / - Description: /
- Code Snippets:` bullets separated by `----------` rules, and `query-docs` returns
Markdown documentation with fenced code blocks. Both are genuine success payloads — real
prose, not a quota message, auth failure, or error envelope — so neither entry carries
`_probe_status: "inconclusive"`.

## Shape decisions

Both tools: `unwrap` empty, `return_model: null`, `fields: {}`, `source: "live"`. There is
no vendor envelope to strip and no record to type — the response *is* the prose. Setting
an unwrap path would have been inventing one, and `_dig` would then return a field the
wrapper never had; `return_model` may not name a primitive, so `null` is the honest
value. `_observed_shape: "str"` was deliberately **kept** rather than deleted: it is the
evidence distinguishing a genuine text-returning tool from an unobserved one. This server
is `no_shaped_tool_by_design`.

`probed_args` needed no scrubbing — `Next.js`, `/vercel/next.js`, and the two free-text
queries are public, functional values matching no PII pattern, and the roundtrip verifier
replays them as-is.

## Verification

Regeneration auto-detected `context7.shapes.json` (2 tools) and rewrote the module.
`ast.parse` succeeded; both `query_docs` and `resolve_library_id` are typed `-> Any`,
which is the correct and intended result for a prose-only server. `run.py` is the
harness's own responsibility and was not generated here.
