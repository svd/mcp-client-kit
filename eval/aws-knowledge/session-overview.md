# aws-knowledge — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:07:38Z
- **Duration:** 4m 9s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

The server exposes **5 tools**, every one carrying `annotations.readOnlyHint: true` and
`destructiveHint: false`. Nothing was classified as mutating, so nothing was skipped for
safety: all 5 were selected and probed. Seed commands: none.

## Discriminators

`mcpgen list --schema` emitted no discriminator advisory — no scalar parameter name is shared
across two tools. The description sweep found two candidates the advisory cannot see by
construction:

- **`resource_type` on `get_regional_availability`** — a single-tool discriminator that
  declares itself in prose ("Response key: products | service_apis | cfn_resources"). All
  three values were probed separately and each returned a distinct top-level key. Confirmed;
  resolved with option 1 (variants + overloads).
- **`topics` on `search_documentation`** — an **array** discriminator, invisible to the
  advisory. Default topics returned `{rank_order, title, context, url}`; `topics:
  ["agent_skills"]` returned `{rank_order, title, skill_description, skill_name}` instead.
  Confirmed by shape difference, but an array param can request several topics at once, so
  overloads cannot describe it. Resolved with option 2: one union base model, `total=False`,
  built from a two-`--args` probe so both variants merged.

## Surprises

Every tool wraps its payload in the same vendor envelope, `content.result` — except
`retrieve_skill`, which returns `content.skill_content` with no `result` level. All three
`get_regional_availability` variants keyed their payload by the caller's own filter string
(`{"Amazon Bedrock": {"status": ...}}`, `{"AWS::S3::Bucket": "str"}`), so the inner value is
caller-dependent and stays `dict`. Note the two payload types differ across variants: `product`
nests a `{status}` object, while `api` and `cfn` return a bare string. `next_token` and
`failed_regions` were observed only as `None`.

## Shape decisions

| Tool | unwrap | return | why |
|---|---|---|---|
| `list_regions` | `content.result` | `list[RegionSummary]` | 37 flat `{region_id, region_long_name}` records |
| `search_documentation` | `content.result` | `list[SearchDocumentationItem]` | union base model over both `topics` variants |
| `read_documentation` | `content.result` | `list[DocumentationPage]` | batched request/response list; `redirected_url`/`error_code` seen as null → nullable |
| `retrieve_skill` | `content` | `SkillDocument` | envelope has no `result` level; single `skill_content` string |
| `get_regional_availability` | `content.result` | `ProductAvailability` / `ServiceApiAvailability` / `CfnResourceAvailability` | three probed variants, scalar discriminator |

Nothing was left as `Any`. No probe returned `"str"`, an error, or a traceback, so no
`_probe_status: inconclusive` marker was needed. `probed_args` held only public AWS region
codes, doc URLs, and catalog names — no PII to scrub.

## Verification

Regenerated with the shape-spec present; `ast.parse` succeeded. `get_regional_availability`
emits three `@overload` stubs keyed on `Literal['api'|'cfn'|'product']` over a union impl, and
every shaped body digs the envelope via `_dig` / `_dig_list`. `run.py` is the harness's job and
was not generated here.
