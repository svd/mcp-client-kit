# stackoverflow — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-26T09:21:26Z
- **Duration:** 3m 14s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

The server exposes **2 tools**, both carrying `annotations.readOnlyHint: true`:
`get_content` and `so_search`. Both were probed; nothing was skipped. No mutating
tools exist, so the subagent no-mutation fallback cost no coverage. No seed commands
applied. Native streamable-HTTP OAuth was already authorized in
`~/.mcpgen/credentials.json`; every call went through `mcpgen`, and the token
refreshed silently — no re-login was needed.

`mcpgen list` raised no discriminator advisory. The only shared input parameter is
`query`, a free-text string that never appears as a response key, so Pass 2 of the
discriminator filter discards it.

## Probing

Bootstrap ran first: `mcpgen call so_search` captured a raw payload, from which real
`question_id`/`answer_id` values were read to build `get_content` requests
(`SO_Q54987361`, `SO_A54987732`). Each tool was then multi-probed twice so optional
fields would widen rather than be typed from a single sample.

Two surprises:

1. **Inconsistent envelope casing across tools on the same server.** `so_search`
   returns `{"items": [...]}` (lowercase); `get_content` returns
   `{"Items": [...], "Errors": [...]}` (PascalCase). The unwrap paths differ purely
   in case — exactly the kind of thing an input schema cannot tell you.
2. **`get_content.Data` is polymorphic by a *response* key.** Each item carries
   `Type` ∈ {Question, Answer, Comment} and a `Data` object whose keys vary with it —
   a Question `Data` has `question_id`/`view_count`/`answer_count`, an Answer `Data`
   has `answer_id`/`is_accepted` and no view counts. Because `Type` is not an input
   parameter, codegen's overload machinery cannot key on it.

Optional-field evidence showed up in `so_search`: `answers` appeared in 3 of 4 items
and `accepted_answer_id` in 2 of 4, so both stay optional under `total=False`.

## Shape decisions

- **`so_search`** → `unwrap: ["items"]`, `return_container: "list"`,
  `return_model: SearchQuestionItem`. Top-level scalars promoted; `tags`, `owner`,
  and `answers` stay `list`/`dict` rather than being modelled two levels deep.
- **`get_content`** → `unwrap: ["Items"]`, `return_container: "list"`,
  `return_model: ContentItem` with `Data: dict`. This is the generic-base-model
  choice: `Data` is left unmodelled instead of committing a variant-specific lie.
  Documented trade-off: unwrapping to `Items` discards the sibling `Errors` array,
  which reports per-request failures for bad ids. `Errors` was `[]` in both probes,
  so its element shape is unobservable and could not have been typed anyway. A
  `Comment` (`SO_C…`) was never probed — no comment id was reachable from search —
  so the `Type` enum is observed only for Question and Answer.

No PII scrub was required: `probed_args` hold public Stack Overflow content ids and
plain search strings, and replacing them would have broken the roundtrip verifier.

## Result

The regenerated module parses cleanly (`ast.parse` OK). Both tools return real types —
`-> list[ContentItem]` and `-> list[SearchQuestionItem]` — digging their envelopes via
`_dig_list`. Zero tools left at `Any`. Runner generation was skipped per the subagent
fallback.
