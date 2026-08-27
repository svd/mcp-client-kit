# stackoverflow — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:04:41Z
- **Duration:** 2m 22s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list stackoverflow --schema` returned **2 tools**, both carrying
`annotations.readOnlyHint: true`: `so_search` (lexical Stack Overflow search) and
`get_content` (fetch questions/answers/comments by `SO_Q…` / `SO_A…` / `SO_C…` id).
Neither name passes the mutating keyword test, so the annotation was not disputed and
both were selected. **2 probed, 0 skipped** — no `## mutating-skipped` section applies.

Native streamable-HTTP OAuth worked without re-auth; the cached token carried the whole
run, and no Cloudflare challenge appeared (engine 0.9.0.dev1, above the 0.8.0 floor the
manifest notes for this server).

**Discriminators: N/A.** The only shared parameter is `query`, which the engine denylists,
so no advisory fired and Pass 2 was skipped.

## Surprises

The two tools disagree on envelope convention, which is the interesting finding here.
`so_search` returns lowercase `{"items": [...]}`. `get_content` returns a PascalCase
`{"Items": [{"Site","Type","Id","Data","OriginalRequest"}], "Errors": []}` — a second
envelope layer, with the actual Stack Overflow record nested one level further under
`Data`, and a per-item `Type` tag.

That `Type` tag is a **response-side** discriminator with no input parameter behind it —
the variant is selected by the `SO_Q…`/`SO_A…` prefix inside the free-text `query`, so the
step-2.e discriminator machinery does not apply. Probing a question id and an answer id
separately confirmed it: the deep-merged `Data` unions question keys (`question_id`,
`answer_count`, `is_answered`, `view_count`) with answer keys (`is_accepted`,
`answer_id`).

`Errors` came back `[]` on all three probes, so its element shape is unobservable; it sits
outside the chosen unwrap path and is not modelled.

## Shape decisions

- **`so_search`** → `unwrap: ["items"]`, `return_container: "list"`,
  `return_model: SearchQuestionItem`. Ten top-level stable scalars promoted
  (`question_id`, `title`, `link`, `body_markdown`, `score`, `view_count`,
  `answer_count`, `is_answered`, `accepted_answer_id`, `creation_date`). `tags`,
  `answers`, and `owner` are non-scalar nests left unmodelled per the depth guard;
  `accepted_answer_id` is absent rather than null on unanswered questions, which
  `total=False` already covers.
- **`get_content`** → `unwrap: ["Items"]`, `return_container: "list"`,
  `return_model: ContentItem`. The wrapper's own scalars (`Site`, `Type`, `Id`,
  `OriginalRequest`) are typed; **`Data` is deliberately left `dict`**. Flattening the
  merged Question∪Answer union into one `TypedDict` would state authoritatively that every
  item carries both `question_id` and `answer_id`, which no single response does. `Type` is
  the honest handle for callers to narrow on.

Distinct model names were minted because the two field sets differ; no collision.

## Verification

`ast.parse` clean. Both wrappers return concrete types — `list[ContentItem]` and
`list[SearchQuestionItem]` — with bodies digging their envelopes via `_dig_list`. No tool
was left `Any`.
