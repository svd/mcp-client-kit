# exa — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-25T19:32:27Z
- **Duration:** 1m 29s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Surface

`mcpgen list exa --schema` reported exactly **2 tools**: `web_search_exa` and
`web_fetch_exa`. Both carry explicit `annotations.readOnlyHint: true`
(`destructiveHint: false`, `idempotentHint: true`), so the step-2 mutation triage was
decided by the primary signal alone — no keyword heuristic needed, nothing flagged
`[MUTATING]`. With only two read-only tools and no `AskUserQuestion` available, the
subagent fallback applied: probe all. **2 probed, 0 skipped.**

No discriminator advisory was emitted. The two tools share no parameters at all
(`query`/`numResults` vs. `urls`/`maxCharacters`), so there were no
polymorphic-suspect siblings to resolve.

## Surprises

The interesting finding is that Exa's public MCP endpoint is **not** the deeply nested,
schema-lied JSON the manifest note anticipated. Both tools returned a **plain markdown
document** as the sole text content part — `_observed_shape: "str"` for both
(14,662 bytes for the search, 374 bytes for the fetch).

I ran the JSON-in-string check the skill mandates for `str` shapes: captured the raw
payloads with `mcpgen call … --out *.probe-raw.json` and attempted `json.loads()`.
Both failed at char 0 — the search payload starts `Title: … / URL: … / Published: … /
Highlights:` and the fetch payload starts `# Example Domain`. This is human-prose
markdown, not a serialized envelope, so `_json_unwrap` does **not** apply and
`_observed_shape: "str"` stands as the honest answer.

Neither payload was a quota or auth error — both are genuine success responses with
real content — so no `_probe_status: "inconclusive"` marker was added. The raw dumps
were deleted after inspection.

## Shape decisions

| Tool | unwrap | return_model | Why |
|---|---|---|---|
| `web_search_exa` | `[]` | `null` | Response is a flat markdown string; there is no record to dig for and no scalar fields to promote. A `TypedDict` here would be a fabricated shape. |
| `web_fetch_exa` | `[]` | `null` | Same — clean-markdown page text, no envelope. |

The only shape-spec edit that earned its place was `input_overrides`: JSON Schema
declares `numResults` and `maxCharacters` as `number` (→ `float`), but both are
counts. Overriding each to `int` makes the regenerated signatures read
`numResults: int | None = None` and `maxCharacters: int | None = None` instead of
`float`.

`probed_args` needed no scrubbing — a generic search phrase and `https://example.com`,
neither PII nor identifying.

## Verification

The regenerated module parses cleanly (`ast.parse` OK, 3,306 bytes). Both wrappers
correctly return `-> Any`, which is the accurate type for a text-only server: shaping
them would be an authoritative lie about a payload that has no structure.
