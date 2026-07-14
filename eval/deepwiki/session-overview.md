# deepwiki Eval Session Overview

## Run Metadata

- **Executed:** 2026-07-14T08:24:03Z
- **Duration:** 2m 45s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server and Tool Inventory

The deepwiki MCP server (`https://mcp.deepwiki.com/mcp`) exposes **3 tools**, all read-only:

- `read_wiki_structure` — returns a formatted text outline of documentation topics for a GitHub repository
- `read_wiki_contents` — returns full markdown wiki content for a GitHub repository
- `ask_question` — asks an AI-powered question about a GitHub repository and returns a text answer

All three tools were probed; none were skipped. No mutating tools were detected (name/description heuristic found no `create`/`update`/`delete`/etc. matches), and `mcpgen list --schema` emitted no discriminator advisory, so no polymorphic-suspect resolution was needed. With only 3 tools the run executed as a single driver thread — no recon or batch subagents were dispatched.

## Probe Results

The server requires no authentication. All probes targeted `facebook/react`, a real, richly-documented public repository chosen to avoid the sparse-fixture problem noted for `octocat/Hello-World`.

**read_wiki_structure**: `{"repoName": "facebook/react"}` → a plain-text hierarchical outline of wiki topics (~1.4 KB), numbered sections/sub-sections covering the reconciler, rendering targets, and compiler. Not JSON — `json.loads()` fails, so no JSON-unwrap applies.

**read_wiki_contents**: `{"repoName": "facebook/react"}` → the full markdown documentation body (~659 KB raw payload), by far the largest response. Pure markdown prose, not a JSON envelope — this tool clearly wants topic-scoped follow-up questions in practice rather than being read whole.

**ask_question**: `{"repoName": "facebook/react", "question": "What is the high-level architecture of this repository?"}` → a natural-language, AI-generated answer (~3.7 KB) describing the React Compiler's Babel-plugin architecture. The `repoName` parameter's `anyOf` schema (string or array of up to 10 strings) maps to `Any` in the generated signature, which correctly represents the union.

All three responses were genuine, well-formed content — no quota, rate-limit, or auth errors were observed on any probe.

## Shape Decisions

All three tools return `str` directly with no vendor envelope wrapping:
- `unwrap: []` for all three — no envelope to strip.
- `return_model: null` for all three — plain scalars (`str`), so `TypedDict` modeling does not apply; a model over an opaque string would be a false claim of structure, not real typing.
- `return_container` omitted — no list containers.
- No `input_overrides` needed — parameter types matched the schema.

No PII was found in `probed_args`. `facebook/react` and the architecture question are public/generic values, not personally identifiable information, so no scrubbing beyond the standard check was required.

## Generated Module

The regenerated `deepwiki.py` (2,156 bytes) parsed cleanly with `ast.parse()`. All three wrappers return `-> Any`, accurately reflecting that runtime values are plain `str`. `__schema__` attributes remain embedded on each function for introspection.
