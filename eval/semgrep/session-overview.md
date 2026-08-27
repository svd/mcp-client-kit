# semgrep — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:07:56Z
- **Duration:** 6m 42s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list semgrep --schema` returned **7 tools**, none carrying `annotations`. All 7 were
classified non-mutating (the scan tools analyse code supplied in the call and persist nothing),
so all 7 were selected and probed; none were skipped. No seed commands apply. OAuth was already
established — `semgrep_whoami` answered on the first probe with no login prompt.

Four of the seven are marked `[DEPRECATED]` in their own descriptions.

## Surprises

The four deprecated tools — `get_supported_languages`, `get_abstract_syntax_tree`,
`semgrep_scan_remote`, `semgrep_scan_with_custom_rule` — all returned the **same 473-byte prose
notice** ("The hosted Semgrep MCP server no longer runs scans"), never a result payload. Their
shapes were never observed, so each carries `"_probe_status": "inconclusive"` rather than a
misleading `"_observed_shape": "str"`.

`semgrep_findings` refused a bare call: *"No repositories provided."* With a real repo
(`svd/mcp-client-kit`, public) it returned a `{findings: [...], total_findings: int}` envelope.
When a filter matches nothing it returns the plain string `No findings found` instead of an
empty envelope — recorded in the shape-spec as `_empty_result_sentinel`.

## Discriminator

The `list --schema` advisory fired nothing (no scalar param is shared across two tools). The
description sweep flagged `issue_type` on `semgrep_findings`, and Pass 2 **confirmed** it: SAST
records carry `sastAttributes`/`aiTags`/`ruleset`/`policySlug`/`subcategories`; SCA records carry
`scaAttributes`/`vulnGroupKey`/`relatedIssues`/`note`/`activityHistory`; 35 keys are shared.
`ISSUE_TYPE_SECRETS` could not be observed — the probe repo has no secrets findings.

Because one of three variants was never probed, resolution took **step 4 option 2**: a single
`SemgrepFinding` base model over the 29 top-level scalars common to the two observed variants,
rather than overloads that would have misdescribed SECRETS.

## Shape decisions

| Tool | Unwrap | Return | Why |
|---|---|---|---|
| `semgrep_findings` | `["findings"]` | `list[SemgrepFinding]` | vendor envelope; base model over confirmed-common scalars |
| `semgrep_whoami` | `[]` | `SemgrepIdentity` | flat identity record; nested `authDetails` left out |
| `semgrep_rule_schema` | `[]` | `Any` | genuine ~37 KB YAML text document; `json.loads` on the raw payload raised `JSONDecodeError`, so not double-encoded |
| 4 deprecated tools | `[]` | `Any` | no result payload ever observed |

Nested `repository`, `first_seen_scan`, and `last_seen_scan` were left unmodelled — one probe is
not enough to state depth authoritatively.

## Result

The regenerated module parses cleanly (`ast.parse` OK). `semgrep_findings` reads
`-> list[SemgrepFinding]` and digs via `_dig_list(result, ('findings',))`; `semgrep_whoami` reads
`-> SemgrepIdentity`. Enum params rendered as `Literal[...]` automatically.
