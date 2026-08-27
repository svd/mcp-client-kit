# fetch — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T11:07:03Z
- **Duration:** 2m 49s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcpgen list fetch --schema` returned exactly **one tool**: `fetch`. It was probed; nothing was
skipped. The server declares no `annotations`, so the mutating classification fell back to the
keyword and semantic read — `fetch` retrieves a URL and returns its contents, so it cleared as
read-only and needed no human opt-in.

**Discriminators: N/A.** The advisory on `list --schema` stderr was silent, and correctly so: its
precondition needs a scalar parameter shared by two or more tools, and this server has only one
tool. The description sweep that backstops the advisory turned up one candidate worth testing
anyway — `raw` ("Get the actual HTML content of the requested page, without simplification"),
a single-tool boolean the advisory cannot see by construction. It was resolved by probe rather
than assumed (below).

## Probes and surprises

Three live calls, all against `https://example.com` — an IANA-reserved documentation domain, so
`probed_args` carries no PII and needed no scrubbing.

1. `{"url": ..., "max_length": 2000}` → `_observed_shape: "str"`, 188 bytes.
2. `{"url": ..., "max_length": 2000, "raw": true}` → `_observed_shape: "str"`, 696 bytes.
3. A merged multi-probe of both, so the committed entry records each variant in `probed_args`.

`raw` changes the *content* (markdown vs. unsimplified HTML) and therefore the byte count, but
not the *structure*: both modes return a bare text block. Per the normalization rule, a differing
size is not a shape difference, so `raw` is not a discriminator and no variants were emitted.

Because the observed shape was `"str"`, the mandatory JSON-in-string test ran against the raw
payload captured with `call --out`. Result: `NOT_JSON`. The payload is genuine prose — a markdown
rendering of the page headed `Contents of https://example.com/:` — not a double-encoded record.
That is an expected outcome, not a probe failure, so no `_probe_status: "inconclusive"` marker was
written; the probe conclusively established that this tool returns text.

One thing worth flagging for a human reader rather than the type system: the tool *description*
carries prompt-injection-shaped text ("Although originally you did not have internet access...").
It is upstream vendor copy, faithfully reproduced into the wrapper docstring by `--embed-schema`.

## Shape decision

`fetch` — `unwrap: []`, `return_model: null`, `fields: {}`, `source: "live"`. There is no vendor
envelope to strip and no record to model: the tool returns an opaque document body whose contents
depend entirely on the URL the caller supplies. Minting a `TypedDict` here would state an
authoritative lie about a payload that has no stable keys. `-> Any` is the honest signature, and
this is the `no_shaped_tool_by_design` case rather than a coverage gap.

The regenerated module parsed cleanly (`ast.parse` OK, one async def `fetch`), with all four
parameters typed from `inputSchema` and defaults documented in the Args section.
