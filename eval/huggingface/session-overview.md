# Session Overview: huggingface MCP Server

## Run Metadata

- **Executed:** 2026-07-14T08:27:49Z
- **Duration:** 4m 14s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Tool Inventory

The HuggingFace MCP server currently exposes **7 tools**: `hf_whoami`, `space_search`, `hub_repo_search`, `hub_repo_details`, `hf_fs`, `hf_doc_search`, `hf_doc_fetch`. **All 7 were probed; none were skipped** — none matched the mutating-keyword heuristic.

Note on drift: a prior eval of this server (2026-06-19) recorded 8 tools, including `paper_search` and an image-generating `gr1_z_image_turbo_generate` (Space-backed, dynamic tool). Neither appeared in this run's `mcpgen list` output — the server's tool surface has changed. This eval reflects only tools live on the server at run time; nothing was fabricated from stale prior-run data.

## Probing Results

The dominant finding, consistent with the prior run, is that **all 7 probed tools return pre-formatted Markdown/plain-text strings**, not structured JSON — even tools that clearly wrap structured data internally (`hub_repo_search`, `hub_repo_details`, `space_search`). `_observed_shape: "str"` was accurate and honest for every tool; JSON-in-string detection (`json.loads()`) was applied and failed on all payloads, confirming genuine text rather than serialized JSON.

One wrinkle: `hf_doc_search` was probed with three queries. An empty query (documented "discovery mode") returned a clean Markdown catalog of ~60 doc products. A semantic query (`"transformers pipeline"`) triggered a transient `500 Internal Server Error`; a retry with a different query (`"quicktour"`) succeeded immediately, confirming the 500 was query-specific/transient, not systemic auth/quota failure. Since not every non-empty response was an error, `_probe_status: "inconclusive"` wasn't warranted; a `_probe_note` records the transient failure instead. `hf_doc_fetch` was then probed with a real doc URL surfaced from the successful search, returning actual document text.

`hf_whoami` returned only anonymous-usage guidance (join/settings links) — no identity to scrub.

## Shape Decisions

Since all probed tools return `str`:

- **`unwrap`:** `[]` for all tools — no vendor envelope; the MCP `text` content field is the final string value.
- **`return_model`:** `null` for all tools — a `TypedDict` isn't applicable to plain string responses; per the skill's guard, no primitive-name model was fabricated.
- **`return_container`:** omitted — no list of records returned.
- **`fields`:** `{}` for all tools — no stable scalar fields to extract from a string.

No discriminator candidates were flagged by `mcpgen list --schema` (no stderr advisory), and none were found by inspection — `limit` recurs across tools but is a pagination parameter, auto-disqualified. `probed_args` required no PII scrubbing: all values are public strings (repo IDs like `bert-base-uncased`/`squad`, search terms, `hf://` URIs, doc URLs).

## Generated Module

The regenerated `huggingface.py` (13.0 KB, 7 async functions) parsed cleanly via `ast.parse`. A second `codegen` invocation with identical inputs produced byte-identical output (idempotency confirmed by diff). All functions return `-> Any`, the honest type given every tool returns unstructured text — no TypedDict models were warranted. `eval-kit verify huggingface` passed `ast`, `signatures`, `idempotency`, and `pii`; `roundtrip` was skipped (no shaped, non-mutating tool exists to round-trip). Final verdict: **pass**.
