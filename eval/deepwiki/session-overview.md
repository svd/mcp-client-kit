# Session Overview: deepwiki

## Run Metadata

- **Executed:** 2026-08-25T15:42:11Z
- **Duration:** 1m 50s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Summary

The `deepwiki` MCP server (HTTP, `https://mcp.deepwiki.com/mcp`, no auth) exposes 3
tools. All 3 were read-only (no `create`/`update`/`delete`-shaped names, no
`readOnlyHint: false`), so all 3 were selected and probed under the subagent
"probe all non-mutating tools" fallback — no tools were skipped. None of the tools
shared a discriminator-shaped parameter, so no polymorphic-variant resolution was
needed.

## Tool-by-tool probe results

- **`read_wiki_structure`** — probed with `repoName: "modelcontextprotocol/servers"`.
  Returned a ~1.2 KB Markdown-formatted outline of documentation topics as a plain
  string (a nested bullet list of page titles). Not JSON, not JSON-in-a-string
  (`json.loads()` fails on it) — genuinely unstructured prose.
- **`ask_question`** — probed with the same repo and the question "What is the
  purpose of this repository?". Returned a ~2.5 KB free-text, AI-generated answer.
  Same as above: prose, not structured data.
- **`read_wiki_contents`** — probed with the same repo. Returned the full wiki body
  as a single ~394 KB Markdown string — by far the largest payload of the three,
  and the reason this tool is a strong "big dump" candidate to keep out of model
  context in downstream callers, even though its shape (a bare string) needed no
  further typing.

## Shape decisions

All three tools got the same shape decision: **`unwrap: []`, `return_model: null`**.
Each tool's entire response is a single Markdown/prose string with no vendor
envelope, no JSON structure, and no stable scalar fields to promote into a
`TypedDict`. Typing any of them as a record would be an authoritative lie about a
shape that is, by design, natural-language text. The generated wrapper functions
correctly keep `-> Any` for all three — callers should treat the return value as a
plain string and handle it (render, chunk, summarize) at the call site.

No PII appeared in `probed_args` — `repoName` values are public GitHub repository
identifiers and the `question` string is a generic prompt, so no scrubbing was
required beyond the standard pass.

## Module generation

`mcpgen codegen` regenerated `eval/deepwiki/deepwiki.py` from the shape spec
without errors. The module parses cleanly (`ast.parse` succeeds), and all three
signatures (`ask_question`, `read_wiki_contents`, `read_wiki_structure`) correctly
read `-> Any`, matching the shape-spec's `return_model: null` for all three tools.
