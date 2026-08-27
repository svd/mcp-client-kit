# aws-knowledge — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T09:39:56Z
- **Duration:** 5m 20s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list` reported **5 tools**, every one carrying `annotations.readOnlyHint: true` and
`destructiveHint: false`. No mutating tools, so nothing was skipped for safety and **all 5 were
probed**. No seed commands were configured, and none were needed — the server is a public,
stateless AWS documentation endpoint.

## Discriminators

The `list --schema` stderr advisory stayed silent, correctly: no scalar parameter is shared by
two or more tools. The description sweep caught what the advisory structurally cannot — a
discriminator confined to a single tool. `aws___get_regional_availability` declares it in prose:
*"Response key: products | service_apis | cfn_resources"*, keyed on `resource_type`
(`product` | `api` | `cfn`). Three paced probes against `us-east-1` confirmed it: each value
returns a different top-level key **and** a different value type — `products` maps to
`{status: str}` records, while `service_apis` and `cfn_resources` map to bare strings. Resolved
via option 1 (probe all variants); codegen emitted three `@overload` stubs over `Literal[...]`.

## Surprising response

`aws___search_documentation` returns a **heterogeneous list**. One response mixes doc hits
(`rank_order`, `title`, `context`, `url`) with agent-skill hits (`rank_order`, `title`,
`skill_description`, `skill_name`). The mix is not selected by any argument — a
`topics: ["troubleshooting"]` call returned one skill hit at rank 1 and two doc hits below it.
`_observed_shape` renders only the first list element, so a single probe would have under-typed
this either way. Modelled as one `total=False` union (`SearchResultItem`) rather than a
discriminated variant set, since no argument selects the kind.

## Shape decisions

| Tool | unwrap | return |
|---|---|---|
| `list_regions` | `content.result` | `list[AwsRegion]` |
| `search_documentation` | `content.result` | `list[SearchResultItem]` (union, see above) |
| `read_documentation` | `content.result` | `list[DocPage]` — `redirected_url` / `error_code` observed `None`, typed `str \| None` |
| `get_regional_availability` | `content.result` | 3 overloads: `RegionalProductAvailability` / `RegionalApiAvailability` / `RegionalCfnAvailability` |
| `retrieve_skill` | `content.skill_content` | unwrap-only `Any` — the payload is a markdown **string**, so `return_model` stays `null` |

The regional maps are keyed by unbounded catalog names, so they stay `dict[str, ...]` rather
than fabricated `TypedDict`s. `failed_regions` was only ever observed `None` → `Any | None`.

## Verification

The regenerated module parsed cleanly under `ast.parse`. All four record-carrying tools return
typed models; `retrieve_skill` is honestly `Any`. `probed_args` needed no scrubbing — every
value is a public AWS region code, doc URL, or registry skill name.
