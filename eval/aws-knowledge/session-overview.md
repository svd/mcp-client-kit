# aws-knowledge — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-25T19:32:25Z
- **Duration:** 2m 38s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Tool inventory

The server exposes **5 tools**, all carrying `annotations.readOnlyHint: true` with
`destructiveHint: false`. Nothing was mutating, so all 5 were probed and all 5 were
shaped — none skipped, none left `Any`. `mcpgen list` emitted no discriminator advisory
(no parameter is shared across two or more tools).

## Interesting findings

Every response is wrapped in a uniform vendor envelope: `content.result` for the four
list/collection tools, and a bare `content` for `retrieve_skill` (whose payload key is
`skill_content`, not `result`). Two responses were more polymorphic than the input
schema suggested:

- **`search_documentation`** changes its item shape by `topics`. A `general` query returns
  `{rank_order, title, context, url}`; an `agent_skills` query returns
  `{rank_order, title, skill_description, skill_name}` — `context`/`url` vanish entirely.
  `topics` is a list, not a scalar, so it is not a codegen discriminator. Both variants were
  multi-probed in one call and deep-merged into a single `total=False` union.
- **`get_regional_availability`** switches its response key on `resource_type`:
  `product → products`, `api → service_apis`, `cfn → cfn_resources`. All three variants
  were probed live. Each inner value is an open-ended catalog keyed by AWS product name,
  `SdkServiceId+Operation`, or CFN type — hundreds of dynamic keys, unmodelable as a
  `TypedDict`.

## Shape decisions

| Tool | Unwrap | Model | Why |
|---|---|---|---|
| `list_regions` | `content.result` | `list[Region]` | Clean two-field records (`region_id`, `region_long_name`). |
| `read_documentation` | `content.result` | `list[DocumentationPage]` | Stable scalars: `status`, `url`, `content`, `total_length`, `start_index`, `end_index`, `truncated`. `redirected_url` came back `None`, typed `str \| None`. Error responses add `error_code`, covered by `total=False`. |
| `search_documentation` | `content.result` | `list[SearchResultItem]` | Union of the general and agent_skills item shapes; every field optional. |
| `retrieve_skill` | `content` | `SkillDocument` | Single record, one field `skill_content: str`. Envelope has no `result` key here. |
| `get_regional_availability` | `content.result` | `RegionalAvailability` | **Generic base model** over the three variants rather than three variant models. All three carry `next_token` and `failed_regions`, plus exactly one of the catalog keys. The catalogs stay `dict` — modelling their dynamic keys would be an authoritative lie. |

Naming used distinct types per tool; no `return_model` collisions.

## Verification

`ast.parse` on the regenerated module succeeded. All 5 signatures return their `TypedDict`
(or `list[...]`) rather than `Any`, and each body digs the recorded envelope via
`_dig` / `_dig_list`. `probed_args` needed no scrubbing — every value is public
(region codes, AWS doc URLs, the public skill name `aws-serverless`). Raw
`*.probe-raw.json` bootstrap dumps were deleted after use. The optional runner step was
skipped per the non-interactive fallback.
