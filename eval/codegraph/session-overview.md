# Session Overview: codegraph

## Run Metadata

- **Executed:** 2026-06-19T22:43:22Z
- **Duration:** 2m 40s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The `codegraph` MCP server is a local code intelligence tool that indexes the current workspace into a SQLite knowledge graph. It exposes **5 tools**, all non-mutating read-only operations. All 5 tools were probed; none were skipped (no mutating tools detected).

## Discriminator Handling

The `mcpgen list` output flagged `projectPath` as a discriminator candidate spanning all 5 tools. This was immediately disqualified at Pass 1 (auto-disqualification) — `projectPath` is explicitly listed in the "Path / repo identity" pattern set (`projectPath`). It is a global context routing argument (pointing to a different codebase) rather than a response shape switch. No discriminator variants needed to be probed.

## Probe Results

All 5 tools return plain Markdown-formatted text (`str`), confirmed via live `mcpgen call` checks on `codegraph_search`, `codegraph_context`, `codegraph_node`, and `codegraph_trace`. The responses are human-readable Markdown strings — headings, code fences, bullet lists — not structured JSON or JSON-in-string payloads. Each tool returned a well-formed response immediately; no quota, auth, or rate-limit errors were encountered. The server connected to a shared daemon at `.codegraph/daemon.sock`.

**Per-tool shape decisions:**

- **`codegraph_search`**: Returns a Markdown listing of matched symbols with locations and snippets. `_observed_shape: "str"`. No unwrap, `return_model: null`. Remains `-> Any`.
- **`codegraph_context`**: Returns a Markdown document with entry points, related symbols, and inlined code blocks. `_observed_shape: "str"`. No unwrap, `return_model: null`. Remains `-> Any`.
- **`codegraph_node`**: Returns a Markdown summary of a single symbol's location, signature, and caller/callee trail. `_observed_shape: "str"`. No unwrap, `return_model: null`. Remains `-> Any`.
- **`codegraph_explore`**: Returns source of related symbols grouped by file in Markdown. `_observed_shape: "str"`. No unwrap, `return_model: null`. Remains `-> Any`.
- **`codegraph_trace`**: Returns a Markdown call-path trace or a failure response with inlined endpoint bodies. `_observed_shape: "str"`. No unwrap, `return_model: null`. Remains `-> Any`.

The skill guards explicitly state that Python primitive names like `str` must not be used as `return_model` values — `null` is correct here. All tools legitimately return `-> Any`.

## Notable Details

- **`codegraph_trace` parameter renaming**: The tool has a `from` parameter (a Python keyword). `mcpgen codegen` handled this automatically — the generated wrapper uses `from_: str` in the signature and translates it back to `{"from": from_}` in the call args. No manual fix needed.
- **`codegraph_search.kind` enum**: Correctly typed as `Literal['function', 'method', 'class', 'interface', 'type', 'variable', 'route', 'component'] | None` from the `inputSchema` enum array.
- **PII scrub**: No PII present in `probed_args`. All probe arguments are generic search terms and symbol names with no UUIDs, emails, or personal identifiers.

## Final Module

`eval/codegraph/codegraph.py` parsed cleanly with `ast.parse`. All 5 async functions are present with correct signatures. The module is importable and structurally sound.
