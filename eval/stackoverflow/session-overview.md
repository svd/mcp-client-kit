# stackoverflow — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:44:58Z
- **Duration:** 3m 37s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server and tool inventory

`https://mcp.stackoverflow.com` over native streamable-HTTP OAuth. A stored credential was
already valid (`mcpgen list-creds` → `stackoverflow valid 2026-08-27T12:14:00`), so no browser
login was needed and the whole run stayed non-interactive.

The server exposes **2 tools**, both carrying `annotations.readOnlyHint: true`:

```
Tools on stackoverflow:
  get_content — Get Stack Overflow Content using the given query (SO_Q123, SO_A456, SO_C789)
  so_search   — Search Stack Overflow using lexical search
```

Both were selected and probed; **nothing was skipped**. No mutating tools exist on this server,
so the subagent mutating-tool fallback never engaged. No seed commands were configured.

**Discriminators: N/A.** The only parameter the two tools share is `query`, which is on the
engine's own denylist, so no candidate could clear the precondition and `list --schema` emitted
no advisory. Pass 2 was correctly skipped.

## Probe findings

`so_search` returned a single-key envelope `{"items": [...]}` with 4 question records. Two
fields proved genuinely optional across the four items: `accepted_answer_id` (2/4) and
`answers` (3/4) — exactly the nullability material this server was picked for. The deep merge
unioned them in, and `total=False` on the emitted `TypedDict` states them honestly.

`get_content` was the surprise. It does **not** reuse the search envelope: it returns
`{"Items": [...], "Errors": []}` — PascalCase, a different key, and a per-request wrapper
carrying `Site`, `Type`, `Id`, `OriginalRequest` around a nested `Data` payload. `Data` is
polymorphic on the *response* field `Type`: the `Question` variant carries
`question_id`/`view_count`/`answer_count`/`tags`, the `Answer` variant carries
`answer_id`/`is_accepted` and drops them. A raw `call` capture confirmed both variants in one
response.

## Shape decisions

- **`so_search`** — `unwrap: ["items"]`, `return_container: "list"`, model `SearchQuestionItem`.
  Ten top-level scalars promoted; `tags`/`answers` kept as `list` and `owner` as `dict` per the
  depth guard rather than modelled from one probe.
- **`get_content`** — `unwrap: ["Items"]`, `return_container: "list"`, model `ContentItem`. The
  four envelope scalars are promoted; **`Data` stays `dict`**. The discriminator here is a
  response field, not an input argument, and a single call can return mixed `Type` values in one
  list, so the shape-spec `variants` mechanism does not apply — typing `Data` from either variant
  would misdescribe the other. Unwrapping to `Items` discards `Errors`, which was empty on this
  probe; noted as a known trade-off.

Bootstrapping used real public Stack Overflow ids (`SO_Q54987361`, `SO_A54987732`) read from the
`so_search` raw capture. `probed_args` holds only public identifiers and a free-text query — no
PII to scrub.

The regenerated module **parses cleanly** (`ast.parse` OK); both tools return
`list[<TypedDict>]` via `_dig_list`, neither is left `Any`.
