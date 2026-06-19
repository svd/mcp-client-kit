# context7 Eval Session Overview

## Run Metadata

- **Executed:** 2026-06-19T22:43:22Z
- **Duration:** 2m 23s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The context7 MCP server (`npx -y @upstash/context7-mcp`, v3.2.1) exposes exactly **2 tools**. Both were probed; neither was mutating.

## Tools

- `resolve-library-id` — Resolves a library name to a Context7-compatible library ID (e.g., `/psf/requests`). Required args: `query` (search intent), `libraryName` (official library name).
- `query-docs` — Fetches documentation snippets for a given library ID. Required args: `libraryId` (e.g., `/psf/requests`), `query` (topic to retrieve docs for).

## Probe Results

Both tools were probed with realistic non-PII args:

- `resolve-library-id` probed with `{"query": "HTTP client library for Python", "libraryName": "requests"}` — returned a formatted plaintext list of matching libraries with metadata (title, Context7 ID, description, snippet count, reputation, benchmark score). The response is a multi-line string formatted with `----------` separators, not a JSON object.
- `query-docs` probed with `{"libraryId": "/psf/requests", "query": "How to make a GET request"}` — returned a markdown-formatted string containing documentation sections with code examples, each preceded by a heading and source URL. The response is documentation prose, not structured JSON.

No quota errors, auth failures, or unexpected empty responses were encountered. Both calls succeeded immediately.

## Shape Decisions

Both tools surface `_observed_shape: "str"`. This is the genuine return type — the server formats all responses as human-readable text (not as structured JSON objects). Specifically:

- Neither response parses with `json.loads()` — the JSON-in-string path does not apply.
- No vendor envelope wrapping is present; the MCP text content arrives directly as a string.
- `unwrap: []`, `return_model: null`, `fields: {}` for both tools.
- No `TypedDict` models are warranted; callers receive a formatted string and parse it themselves if needed.

There were no discriminator candidates flagged by `mcpgen list`. Both tools take independent required args with no shared param that drives shape variation.

`probed_args` in `shapes.json` contains no PII — the args are generic query strings and a public library ID (`/psf/requests`). No scrubbing was required.

## Generated Module

The final `eval/context7/context7.py` (6498 bytes) parsed cleanly with `ast.parse`. Both functions carry `-> Any` return types, which is correct and honest for text-returning tools. The `__schema__` attributes embed the full `inputSchema` for each tool. The module is ready for use with any `McpCaller` implementation.
