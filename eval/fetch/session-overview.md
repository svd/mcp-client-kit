# fetch — session overview

## Run Metadata

- **Executed:** 2026-08-27T06:02:04Z
- **Duration:** 2m 27s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Tool surface

`mcpgen list fetch --schema` returned exactly **one tool**: `fetch`. It was probed;
nothing was skipped. The server publishes no `annotations`, so the step-2b fallback
applied: `fetch` is a read verb matching no mutating keyword, so the tool was cleared as
non-mutating. No seed commands were configured.

`discriminators: N/A`. The advisory precondition needs a scalar parameter declared by
two or more tools under the same name; a one-tool server cannot satisfy it, and the
`list --schema` stderr carried no advisory. Pass 2 was skipped outright.

## Probing

Two probes went out in a single invocation against `https://example.com` — a public
documentation domain, so nothing in `probed_args` is PII and the scrub pass changed
nothing. The pair varied `raw` (`false` → simplified markdown, `true` → source HTML) to
see whether the flag switched the response shape. It does not: both merged to
`_observed_shape: "str"`, 696 bytes.

The one interesting result is a negative one. Content-retrieval servers frequently
double-encode — the record arrives as a JSON string inside the MCP envelope — so the raw
payload was captured with `mcpgen call --out` and run through the guarded JSON-in-string
test, which returned `NOT_JSON (JSONDecodeError)`. The payload is prose:

```
Contents of https://example.com/:
This domain is for use in documentation examples ...
```

That is a genuine success payload, not an error page, a quota message, or an auth
failure — so `_probe_status: "inconclusive"` would be wrong here and was not written.
`_observed_shape: "str"` is kept as the evidence that the shape was actually observed and
is genuinely text.

## Shape decisions

| Tool | unwrap | return_model | Why |
|---|---|---|---|
| `fetch` | `[]` (none) | `null` | Response is a bare markdown/HTML string with no vendor envelope. There is no key path to the record because the payload *is* the record. |

`return_model` stays `null` rather than being set to `str`: the skill forbids naming a
Python primitive as a return model, and a `TypedDict` would claim a dict the wrapper never
returns. Inventing an `unwrap` path to force runtime parsing was explicitly rejected —
`_dig` would then return a field instead of the record. `fields` stays empty; a string has
no top-level scalars to promote. `input_overrides` is empty — the declared `integer` and
`boolean` types match what the server accepts.

This is the `no_shaped_tool_by_design` case: the single tool returns prose, so `-> Any` is
the honest signature, not a coverage gap.

## Verification

The regenerated module parsed cleanly (`ast.parse` OK). Codegen re-consumed the shape-spec
(`shapes: eval/fetch/fetch.shapes.json (1 tool(s))`) and, correctly finding no
`return_model`, emitted the module byte-identical to the pre-shape stub at 2328 bytes —
the expected outcome when the judgment pass concludes there is nothing to shape.
`run.py` is the harness verify stage's job and was not generated here.
