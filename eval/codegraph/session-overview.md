# Session Overview: codegraph

## Run Metadata

- **Executed:** 2026-07-14T08:24:06Z
- **Duration:** 2m 1s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The `codegraph` MCP server is a local code-intelligence tool that indexes the workspace into a SQLite knowledge graph. It exposes **5 tools**, all non-mutating read-only operations. All 5 were probed; none were skipped (no mutating tools matched the keyword heuristic).

## Discriminator Handling

`mcpgen list --schema` flagged `projectPath` as a discriminator candidate spanning all 5 tools. It was disqualified at Pass 1 — `projectPath` matches the "Path / repo identity" pattern set verbatim, a context-routing argument (point at a different indexed codebase) rather than a response-shape switch. No variant probing was required.

## Probe Results

Each tool was probed once, live, against this repo itself, e.g. `codegraph_search(query="verify")`, `codegraph_node(symbol="check_roundtrip")`, `codegraph_trace(from="check_roundtrip", to="CheckResult")`. Every probe returned a well-formed response immediately — no quota, auth, or rate-limit errors. All 5 responses were **rendered Markdown text** (headings, code fences, bullet lists) rather than structured JSON, and none parsed via `json.loads()` (ruling out the JSON-in-string case). This matches codegraph's design intent: its tools hand an LLM narrative context directly, not machine-parseable records.

`codegraph_trace` was the most interesting probe: no static call path exists between `check_roundtrip` and `CheckResult` (related by a return-type annotation, not a call edge). The tool honestly reported the break and inlined both endpoints' bodies plus their file-level siblings — useful signal, still delivered as text.

**Per-tool shape decisions** (all identical: `_observed_shape: "str"`, `unwrap: []`, `return_model: null`, remains `-> Any`):

- **`codegraph_search`** — Markdown listing of matched symbols with locations/snippets.
- **`codegraph_context`** — Markdown document of entry points, related symbols, and inlined code.
- **`codegraph_node`** — Markdown summary of one symbol's location, signature, and caller/callee trail.
- **`codegraph_explore`** — Markdown source of related symbols grouped by file.
- **`codegraph_trace`** — Markdown call-path trace, or a failure response with inlined endpoint bodies.

None of the five had a structured record to promote to a `TypedDict` — `-> Any` is honest and correct, not an under-modeled gap. Per skill guards, `return_model` was left `null` rather than set to the primitive name `"str"`.

## Notable Details

- **`codegraph_trace` parameter renaming**: `from` is a Python keyword; `mcpgen codegen` handled it automatically — the wrapper signature uses `from_: str` and translates it back to `{"from": from_}` in the call body.
- **`codegraph_search.kind` enum**: correctly typed as `Literal['function', 'method', 'class', 'interface', 'type', 'variable', 'route', 'component'] | None`, derived from the `inputSchema` enum array.
- **PII scrub**: no PII in any `probed_args` — all probe arguments are generic code-search terms and symbol names from this repo's own public source (`verify`, `check_roundtrip`, `CheckResult`), so no scrubbing was required.

## Final Module

`eval/codegraph/codegraph.py` parsed cleanly with `ast.parse`; all 5 async functions have correct signatures. `eval-kit verify codegraph` passed all applicable checks (`ast`, `signatures`, `idempotency`, `pii`); `roundtrip` was skipped (`no_shaped_non_mutating_tool` — no tool returns a typed shape, since all five legitimately return `Any`). Overall verdict: **pass**.
