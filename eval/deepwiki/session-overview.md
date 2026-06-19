# deepwiki Eval Session Overview

## Run Metadata

- **Executed:** 2026-06-19T22:43:21Z
- **Duration:** 2m 46s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server and Tool Inventory

The deepwiki MCP server (`https://mcp.deepwiki.com/mcp`) exposes **3 tools**, all read-only:

- `read_wiki_structure` — returns a formatted text outline of documentation topics for a GitHub repository
- `read_wiki_contents` — returns full markdown wiki content for a GitHub repository
- `ask_question` — asks an AI-powered question about a GitHub repository and returns a text answer

All three tools were probed. No tools were skipped. No mutating tools were detected.

## Probe Results

The server requires no authentication. All probes used `microsoft/vscode` as the target repository, chosen as a well-known, richly-documented repo likely to return substantive responses.

**read_wiki_structure**: Returned a plain-text hierarchical outline of wiki topics (approximately 2.5 KB). The response is a formatted list (not JSON), structured as numbered sections and sub-sections of the VSCode documentation. `json.loads()` would fail — no JSON-unwrap applicable.

**read_wiki_contents**: Returned a large markdown document (approximately 1.2 MB) covering all wiki pages for the repository. The response is pure markdown text, not a JSON envelope. This is the largest response by far and suggests this tool should be used with specific topic filters in practice.

**ask_question**: Returned a plain-text AI-generated answer. The `repoName` parameter accepts either a single string or a list of strings (up to 10 repos), reflected in its `anyOf` schema. The probe used a single string. Response was a natural-language answer, not structured data.

## Shape Decisions

All three tools return `str` directly with no vendor envelope wrapping:
- `unwrap: []` — no envelope to strip
- `return_model: null` — tools return plain scalars (`str`), not dicts; TypedDict modeling is not applicable per skill rules (never set `return_model` to a Python primitive name)
- `return_container` omitted — no list containers
- No `input_overrides` needed — parameter types matched the schema
- No discriminator candidates detected (no shared polymorphic parameters across tools)

The `ask_question` tool's `repoName` parameter uses an `anyOf` schema (string or array of strings), which codegen maps to `Any` in the generated signature. This is correct behavior — the union type cannot be expressed as a single Python type without additional annotation.

No PII was found in `probed_args`. The values `microsoft/vscode` and `What is the main architecture?` are public identifiers and generic text, not personally identifiable information.

## Generated Module

The generated `deepwiki.py` (2,156 bytes) parsed cleanly with `ast.parse()`. All three wrappers return `-> Any`, which accurately reflects that the actual runtime values are `str` (plain text). The `__schema__` attributes are embedded on each function for introspection.

No surprises were encountered. The server responded promptly with no auth errors, quota messages, or rate-limit responses.
