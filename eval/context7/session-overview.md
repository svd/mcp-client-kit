# context7 Eval Session Overview

## Run Metadata

- **Executed:** 2026-07-14T08:24:04Z
- **Duration:** 2m 59s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The context7 MCP server (`npx -y @upstash/context7-mcp`, v3.2.3) exposes exactly **2 tools**. Both were probed; neither is mutating.

## Tools

- `resolve-library-id` — Resolves a library/package name to a Context7-compatible library ID and returns matching candidates. Required args: `query` (search intent), `libraryName` (official library name).
- `query-docs` — Retrieves documentation and code examples for a given library ID. Required args: `libraryId` (e.g., `/reactjs/react.dev`), `query` (topic to retrieve docs for).

No tools were skipped — the skill's mutating-keyword heuristic (`create`, `update`, `delete`, `send`, etc.) does not flag either tool, and `mcpgen list --schema` surfaced no discriminator candidates spanning the two tools, so no polymorphic-suspect resolution was needed.

## Probe Results

Both tools were probed with realistic, non-PII args:

- `resolve-library-id` probed with `{"query": "how to use hooks", "libraryName": "React"}` — returned a formatted plaintext list of five matching libraries (`/reactjs/react.dev`, `/react/react`, `/websites/react_dev`, etc.), each block giving title, Context7 ID, description, snippet count, source reputation, and benchmark score, separated by `----------` delimiters.
- `query-docs` probed with `{"libraryId": "/reactjs/react.dev", "query": "React useEffect cleanup function examples"}` — returned a markdown-formatted string with multiple documentation sections, each preceded by a heading, a `Source:` URL, a description, and a fenced code example.

Nothing surprising: no quota errors, auth failures, or empty responses. Both calls succeeded on the first attempt, and a direct `mcpgen call` confirmed the payloads are genuine documentation content, not error strings.

## Shape Decisions

Both tools surface `_observed_shape: "str"`. This is the genuine return type — the server always formats responses as human-readable text, never as structured JSON:

- Neither response parses with `json.loads()`, so the JSON-in-string path does not apply.
- No vendor envelope wraps the payload; the MCP text content arrives directly as a plain string.
- `unwrap: []`, `return_model: null`, `fields: {}` for both tools.
- No `TypedDict` model is warranted for either tool; callers receive prose/formatted text and parse it themselves if needed.

`probed_args` in `context7.shapes.json` contains no PII — just generic search-intent strings and a public library identifier (`/reactjs/react.dev`). No scrubbing was required, and the `_observed_shape` diagnostic keys were removed from the final shape-spec once the "plain str" judgment was recorded.

## Generated Module

The final `eval/context7/context7.py` (7114 bytes) parsed cleanly with `ast.parse`. Both functions carry `-> Any` return types, which is correct and honest for text-returning tools. `__schema__` attributes embed the full `inputSchema` for each tool (from `--embed-schema`). `eval-kit verify context7` passed all applicable checks: `ast`, `signatures`, `idempotency`, `pii` all pass; `roundtrip` was skipped (`no_shaped_non_mutating_tool`) since no tool has a shaped return type to validate live. Overall verdict: pass.
