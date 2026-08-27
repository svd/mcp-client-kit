# cloudflare-docs — Session Overview

## Run Metadata

- **Executed:** 2026-08-27T11:10:09Z
- **Duration:** 2m 17s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Server surface

`cloudflare-docs` is a hosted HTTP server (`https://docs.mcp.cloudflare.com/mcp`, no auth)
exposing **2 tools**. Both carry `annotations.readOnlyHint: true`, so the mutating-tool
classification needed no keyword or semantic fallback and nothing was skipped as mutating.
Both tools were selected and probed — 2 probed, 0 skipped.

No seed commands were configured, and none were needed: this is a stateless documentation
server with no store to populate.

**Discriminators: N/A.** The `list --schema` advisory was silent on stderr, and the
precondition confirms why: the two tools share no parameter at all. `search_cloudflare_documentation`
takes a single `query` string (itself on the engine denylist), and `migrate_pages_to_workers_guide`
takes no parameters. The description sweep for a prose-declared response key also came up empty,
so Pass 2 was correctly skipped rather than merely unrun.

## Probe results and the surprise

Both probes succeeded and both returned `_observed_shape: "str"` — 16470 bytes for the search
tool, 5716 for the migration guide. A `"str"` shape is the skill's cue to test for
double-encoding, so both raw payloads were captured with `call --out` (one call per shell
invocation) and tested with the guarded `json.loads` snippet. Both came back `NOT_JSON`.

The interesting detail is *what* the prose is. `search_cloudflare_documentation` returns
XML-ish markup — repeated `<result><url>…</url><title>…</title><text>…</text></result>` blocks —
which looks structured enough to tempt an unwrap. It is not JSON, so `_dig` cannot parse it and
no key path exists to unwrap toward; inventing one would make the wrapper claim a dict it never
returns. `migrate_pages_to_workers_guide` returns a plain Markdown document.

## Shape decisions

Both tools: `unwrap: []`, `return_model: null`, `fields: {}`, `return_container` unset — the
payload *is* the record, and the record is text. Neither entry is marked
`_probe_status: inconclusive`: both probes returned genuine documentation content, not an error,
a quota message, or a 404. The `"str"` here is an honest observation of a text-returning tool,
which is exactly the distinction that field exists to preserve. `_observed_shape` keys were kept
as evidence per the harness artifact rules.

`probed_args` needed no scrubbing — the one recorded value is a generic documentation question
("How do I bind a KV namespace to a Worker?") carrying no PII, ids, or machine-specific paths.

## Verification

The regenerated module parsed cleanly under `ast.parse`. Both wrappers read `-> Any`, which is
the correct and honest outcome here rather than a coverage gap: this server has no shaped tool
by design. Runner generation was left to the harness verify stage, per the eval-harness rule.
