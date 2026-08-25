# context7 Eval Session Overview

## Run Metadata

- **Executed:** 2026-08-25T15:42:10Z
- **Duration:** 1m 32s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The context7 MCP server (`npx -y @upstash/context7-mcp`, v4.0.3) exposes exactly **2 tools**, both marked `readOnlyHint: true` in their annotations — no keyword/semantic fallback was needed to classify them as safe to probe. Both tools were probed; none were skipped.

## Tools

- `resolve-library-id` — Resolves a package/product name to a Context7-compatible library ID and returns matching libraries. Required args: `query`, `libraryName`.
- `query-docs` — Retrieves and queries up-to-date documentation and code examples for a library ID. Required args: `libraryId`, `query`.

`mcpgen list --schema` surfaced no discriminator advisory — the two tools share no overlapping params, so no polymorphic-suspect resolution was needed.

## Probe Results

Both tools were probed with realistic, non-PII args:

- `resolve-library-id` probed with `{"query": "how to use hooks", "libraryName": "React"}` — returned a formatted plaintext list of candidate libraries (`/reactjs/react.dev`, `/react/react`, `/websites/react_dev`, `/websites/react_dev_reference`, …), each block giving title, Context7 ID, description, snippet count, source reputation, benchmark score, and available versions, separated by `----------` delimiters.
- `query-docs` probed with `{"libraryId": "/reactjs/react.dev", "query": "React useEffect cleanup function examples"}` — returned a markdown-formatted string (6.4 KB) with multiple documentation sections, each preceded by a heading, source URL, description, and fenced code example.

Nothing surprising: no quota errors, auth failures, or empty responses. Both calls succeeded on the first attempt. A follow-up `mcpgen call resolve-library-id` (used to source a real library ID for the `query-docs` probe) confirmed the payload is genuine documentation content, not an error string.

## Shape Decisions

Both tools surface `_observed_shape: "str"`, which is the genuine return type — the server always formats responses as human-readable text, never structured JSON:

- Neither response parses with `json.loads()`, so the JSON-in-string path does not apply.
- No vendor envelope wraps the payload; MCP text content arrives directly as a plain string.
- `unwrap: []`, `return_model: null`, `fields: {}` for both tools — no `TypedDict` is warranted since callers receive prose/formatted text to parse themselves.

`probed_args` contains no PII — generic search-intent strings and a public library identifier (`/reactjs/react.dev`) — so no scrubbing was required. The `_observed_shape` diagnostic keys were removed from the final shape-spec once the "plain str" judgment was recorded.

## Generated Module

`eval/context7/context7.py` (7152 bytes) parsed cleanly with `ast.parse`. Both `query_docs` and `resolve_library_id` carry `-> Any` return types, which is correct and honest for text-returning tools. `__schema__` attributes embed the full `inputSchema` for each tool (from `--embed-schema`). `eval-kit verify context7` passed all applicable checks (`ast`, `signatures`, `idempotency`, `pii`); `roundtrip` was skipped (`no_shaped_non_mutating_tool`) since no tool has a shaped return type to validate live. Overall verdict: pass.
