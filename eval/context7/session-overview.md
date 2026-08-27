# context7 — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:03:14Z
- **Duration:** 3m 11s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Engine and transport

Resolved CLI: `uv run mcpgen` (0.9.0.dev1). Server reached through the config form
(`MCPGEN_SERVERS=.mcp.eval.json`), stdio launch `npx -y @upstash/context7-mcp`,
server banner `Context7 Documentation MCP Server v4.0.3`. Auth is `none`; the manifest
forwards an optional `CONTEXT7_API_KEY` but every call succeeded regardless.

## Tool inventory and selection

The server exposes **2 tools**, and both were probed — nothing was skipped.

```
Tools on context7:
  resolve-library-id — Resolve a package/product name to a Context7 library ID
  query-docs         — Retrieve documentation and code examples for a library ID
```

Both carry explicit `annotations.readOnlyHint: true` / `destructiveHint: false`, so the
mutating-tool classification was a clean annotation read with no keyword or semantic
fallback needed. No mutating tools exist on this server, so no seeding was required and
none was configured.

**Discriminators: N/A.** The only parameter shared by both tools is `query`, which sits on
the engine's own denylist, so no advisory fired on `list --schema` stderr — correctly, since
`libraryId` and `libraryName` each appear on exactly one tool. The description sweep found no
parameter that names a response key in prose. Pass 2 was therefore skipped outright.

## Probe results

`resolve-library-id` (`libraryName: "Next.js"`) returned 2095 bytes; `query-docs`
(`libraryId: "/vercel/next.js"`) returned 5302 bytes. Both merged to `_observed_shape: "str"`.

Because a large `"str"` is the classic double-encoding signature, both raw payloads were
captured with `mcpgen call --out` and tested. Both came back **NOT_JSON**: `resolve-library-id`
returns a plain-text catalog of `- Title: / - Context7-compatible library ID: / - Code Snippets:`
lines, and `query-docs` returns Markdown prose with fenced code blocks. The structure is
formatting, not a payload — there is no envelope, no key path, and nothing to dig.

## Shape decisions

Both tools: `unwrap: []`, `return_model: null`, `fields: {}`, `source: "live"`. Inventing a
`TypedDict` here would claim a dict the wrapper never returns; per the skill's JSON-in-string
rule, `unwrap` stays empty so no `_dig` helper is emitted and the string is passed through
untouched. `_observed_shape: "str"` is left in place as honest evidence of a genuine
text-returning tool — not a `_probe_status: inconclusive` case, since both probes returned
real successful payloads.

`probed_args` needed no scrubbing: public library ids and generic documentation questions,
no PII.

## Verification

The regenerated module parsed cleanly (`ast.parse` OK). Both signatures read
`-> Any`, which is the correct outcome: this server is a documentation-prose server by design,
and every one of its tools returns a string.
