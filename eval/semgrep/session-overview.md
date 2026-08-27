# semgrep — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T06:02:22Z
- **Duration:** 7m 10s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface and selection

`mcpgen list semgrep --schema` returned **7 tools**. None carries an `annotations`
block, so the keyword-plus-semantic fallback decided read-only status. No name matched
a mutating verb — `scan` is not on the list, and both scan tools analyse code supplied
in the call rather than writing to the server — so **all 7 were selected and probed, 0
skipped**. The OAuth session established by `mcpgen login semgrep` was still valid; no
credential errors occurred.

`list` printed no discriminator advisory on stderr, and correctly so: the only
enum-typed scalar params (`issue_type`, `status`) are declared by a single tool each,
and the candidate rule needs two or more tools sharing a name.

## Surprises

Four of the seven tools — `get_supported_languages`, `semgrep_scan_remote`,
`semgrep_scan_with_custom_rule`, `get_abstract_syntax_tree` — are marked `[DEPRECATED]`
and now return the **same 473-byte plain-text notice** saying the hosted server no
longer runs scans and pointing users at the local `semgrep mcp` integration. The byte
count was identical across all four, which is what identified them as one canned
response rather than four independent prose returns. This is a permanent, fully
observed `str` return, not a transient failure, so it is recorded as an honest `str`
with `return_model: null` — not as `_probe_status: inconclusive`.

`semgrep_findings` first failed with `No repositories provided`; the manifest carries no
seed, so the repo was taken from the checkout's own git remote. `issue_type` then turned
out to be a **genuine response-shape discriminator** that the advisory could not have
caught: SAST findings carry `ruleset`, `policySlug`, `sastAttributes`; SCA findings
carry `note`, `vulnGroupKey`, `scaAttributes`, `relatedIssues`. `ISSUE_TYPE_SECRETS` was
probed too and returned the bare string `No findings found` — an empty result is prose
here, not `[]`.

## Shape decisions

- **`semgrep_findings`** — envelope `{findings: [...], total_findings: int}` →
  `unwrap: ["findings"]`, `return_container: "list"`. Resolved by **option 1**: explicit
  `discriminator: "issue_type"` with two probed variants, `SastFinding` and
  `ScaFinding`. `ISSUE_TYPE_SECRETS` is deliberately omitted — its shape was never
  observed, and inventing one from the SAST probe is exactly the single-variant lie the
  guard forbids. Only top-level scalars were promoted; `repository`, `first_seen_scan`,
  `last_seen_scan`, `aiTags`, `sastAttributes`, `scaAttributes` stay untyped.
  `probed_args` was scrubbed (`probe_args_scrubbed: true`) because the repo slug carries
  the account login; the real value lives in the gitignored `semgrep.verify.json`.
- **`semgrep_whoami`** — record arrives unwrapped at top level → `SemgrepIdentity`,
  `unwrap: []`. `sub` and `client_id` were observed only as `null` → `Any | None`.
  `authDetails` left untyped per the depth guard.
- **`semgrep_rule_schema`** — a ~35 KB commented YAML document served as a string. The
  JSON-in-string test returned `NOT_JSON`, so `str` stands and no unwrap applies.
- **The four deprecated tools** — `-> Any`, no model, notice recorded in `_note`.

## Verification

Regenerated with `--embed-schema`; `ast.parse` succeeded. `semgrep_findings` emits two
`@overload` stubs returning `list[SastFinding]` / `list[ScaFinding]` over an impl
returning `list[SastFinding | ScaFinding]` and digging via `_dig_list(result,
('findings',))`; `semgrep_whoami` reads `-> SemgrepIdentity`.
