# Session Overview: codegraph

## Run Metadata

- **Executed:** 2026-08-25T15:42:11Z
- **Duration:** 2m 19s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Summary

The `codegraph` MCP server is a local code-intelligence tool that indexes this repo's workspace into a SQLite knowledge graph via a shared daemon. It exposes **5 tools**, all non-mutating read-only operations (`codegraph_search`, `codegraph_context`, `codegraph_node`, `codegraph_explore`, `codegraph_trace`). All 5 were probed; none were skipped — the keyword/semantic heuristic found no mutating verbs, and no `annotations.readOnlyHint` was set on any tool.

`mcpgen list --schema` flagged `projectPath` as a discriminator candidate spanning all 5 tools. It was auto-disqualified at Pass 1 — `projectPath` matches the "Path / repo identity" pattern verbatim (it lets a caller point at a different indexed codebase), a context-routing argument rather than a response-shape switch. No variant probing was required.

## Probe Results

Each tool was probed once, live, against this repo itself. The first probe round used a plausible but nonexistent symbol name (`verify_roundtrip`); `codegraph_search`, `codegraph_node`, and `codegraph_trace` correctly returned "not found" / "no results" responses for it. Rather than accept those empty-result probes as representative, the actual function name was located via `grep` (`check_roundtrip`, `eval_harness/verify.py:259`) and all three tools were re-probed with the corrected symbol, producing genuine non-empty payloads. Final args: `codegraph_search(query="check_roundtrip")`, `codegraph_node(symbol="check_roundtrip")`, `codegraph_context(task="How does the roundtrip verifier work?")`, `codegraph_explore(query="verify.py roundtrip check")`, `codegraph_trace(from="main", to="check_roundtrip")`.

All 5 responses were **rendered Markdown text** (headings, code fences, bullet lists) rather than structured JSON, and none parsed via `json.loads()` — ruling out the JSON-in-string case. This matches codegraph's design intent: its tools hand an LLM narrative context directly, not machine-parseable records.

`codegraph_trace` was the most interesting probe: no static call path exists between `main` and `check_roundtrip` (invoked through argparse dispatch, not a direct call edge). The tool reported the dynamic-dispatch break and inlined both endpoints' bodies plus their file-level neighbors instead of failing silently.

**Per-tool shape decisions** (all identical: `_observed_shape: "str"`, `unwrap: []`, `return_model: null`, remains `-> Any`):

- **`codegraph_search`** — Markdown listing of matched symbols with locations/snippets.
- **`codegraph_context`** — Markdown document of entry points, related symbols, and inlined code for `check_roundtrip`.
- **`codegraph_node`** — Markdown summary of `check_roundtrip`'s location, signature, and caller/callee trail.
- **`codegraph_explore`** — Markdown source of 19 symbols across 4 files grouped by file.
- **`codegraph_trace`** — Markdown dynamic-dispatch-break report with both endpoints inlined.

None of the five had a structured record to promote to a `TypedDict` — `-> Any` is honest and correct, not an under-modeled gap. Per skill guards, `return_model` was left `null` rather than set to the primitive name `"str"`.

## Notable Details

- **`codegraph_trace` parameter renaming**: `from` is a Python keyword; `mcpgen codegen` handled it automatically — the wrapper signature uses `from_: str` and translates it back to `{"from": from_}` in the call body.
- **`codegraph_search.kind` enum**: correctly typed as `Literal['function', 'method', 'class', 'interface', 'type', 'variable', 'route', 'component'] | None`.
- **PII scrub**: no PII in any `probed_args` — all probe arguments are generic code-search terms and symbol names from this repo's own public source, so no scrubbing was required.

## Final Module

`eval/codegraph/codegraph.py` parsed cleanly with `ast.parse`; all 5 async functions have correct signatures. `eval-kit verify codegraph` passed all applicable checks (`ast`, `signatures`, `idempotency`, `pii`); `roundtrip` was skipped (`no_shaped_non_mutating_tool` — all five tools legitimately return `Any`). Overall verdict: **pass**.
