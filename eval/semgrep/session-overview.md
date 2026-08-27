# semgrep — session overview

## Run Metadata

- **Executed:** 2026-08-27T08:43:13Z
- **Duration:** 5m 27s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool inventory

`mcpgen list semgrep --schema` returned **7 tools**, none carrying `annotations`. Classified
by keyword plus semantic read: none mutate server state. The three `semgrep_scan*` /
`get_abstract_syntax_tree` tools analyse code the caller supplies and persist nothing, so they
were treated as safe to probe. **All 7 were probed; 0 were skipped.**

`discriminators: N/A`. The `list --schema` run emitted no advisory on stderr, and no parameter
clears the precondition independently: `code_files` is shared by two tools but is declared
`anyOf[array, null]`, not a top-level scalar, and `rule` / `code` / `language` each appear on a
single tool.

## Surprising responses

The dominant finding is that **the hosted endpoint has been tombstoned for scanning**. Four
tools — `get_supported_languages`, `semgrep_scan_remote`, `semgrep_scan_with_custom_rule`,
`get_abstract_syntax_tree` — each returned the byte-identical 473-byte notice "This tool is
deprecated and will be removed soon. The hosted Semgrep MCP server no longer runs scans",
directing callers to the local `semgrep mcp` server. No result payload was observable behind
any of them.

`semgrep_findings` declares `repos` with `default: []`, but calling it without one fails with
"No repositories provided. User must provide at least one repository to filter by" — the schema
lies about that argument being optional. Re-probed with `repos: ["semgrep/semgrep"]`, it
returned the 19-byte prose "No findings found": this deployment has no scanned repositories, so
the finding-record shape stayed unobservable.

## Shape decisions

- **`semgrep_whoami`** — the one tool yielding a real record. The payload is the record: no
  vendor envelope, so `unwrap: []`. `return_model: SemgrepIdentity`, with the six top-level
  scalars promoted. `sub` and `client_id` were observed only as `null`, so both are typed
  `Any | None` rather than guessed. `authDetails` is a list of nested dicts and was left out of
  `fields` per the don't-model-depth-from-one-probe rule.
- **`semgrep_rule_schema`** — returns a genuine 37 KB YAML document. A guarded `json.loads`
  test reported `NOT_JSON`, so this is prose by design: `_observed_shape: "str"` stands and
  `return_model` stays `null`.
- **The four deprecated tools plus `semgrep_findings`** — marked
  `"_probe_status": "inconclusive"`. Every non-empty response was a refusal or an empty-set
  message, never a result, so nothing about their record shape was established. Leaving them as
  plain `"str"` would falsely claim they are text-returning by design.

## Verification

The regenerated module parses cleanly (`ast.parse` OK). `semgrep_whoami` reads
`-> SemgrepIdentity`; the six remaining wrappers stay honestly `-> Any`. Enum parameters on
`semgrep_findings` rendered automatically as `Literal[...]`. No seeds were configured or run.
