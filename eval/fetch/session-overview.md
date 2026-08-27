# fetch — generate-mcp-wrappers session overview

## Run Metadata

- **Executed:** 2026-08-27T08:41:06Z
- **Duration:** 3m 19s (`T1 - T0`: agent wall time up to this write — the single authoritative duration)

## Surface

`mcp-server-fetch` exposes exactly **one tool**: `fetch`. It was probed; nothing was
skipped. No seed commands were configured, and none were needed — the server holds no
store, it retrieves a URL on demand.

Classification: `fetch` carries no `annotations` block, so the keyword test plus a
semantic read decided it. It performs an outbound HTTP GET and returns the body; it
creates, updates, or deletes nothing on the server. Treated as read-only and probed.

**Discriminators: N/A.** The advisory can only fire where two or more tools share a
scalar parameter name, and this server has one tool. `raw` — the parameter that does
change the response *content* — is a boolean, which fails the type test in any case.

## Probing

Three arg sets went out in a single `probe` invocation against `https://example.com`
(a public documentation domain, no PII): the markdown default at `max_length=2000`,
the same URL with `raw=true`, and a windowed read at `max_length=200, start_index=100`.
All three returned. The deep merge collapsed them to `_observed_shape: "str"`,
`_observed_bytes: 696`.

The `"str"` result triggered the JSON-in-string check. A raw payload was captured with
`mcpgen call --out` and tested with the guarded `json.loads` snippet: **`NOT_JSON`**
(`JSONDecodeError`). The payload is human-readable markdown — a `Contents of
https://example.com/:` header followed by the simplified page text and a link. That is
a genuine success payload, not an error, a quota message, or an auth failure, so no
`_probe_status: "inconclusive"` marker was added; `_observed_shape: "str"` is the
honest record of what the tool returns.

## Shape decisions

- **`fetch`** — `unwrap: []`, `return_model: null`, `return_container` unset,
  `fields: {}`. There is no vendor envelope to dig through and no record to model: the
  tool returns prose by design, in both markdown and `raw` HTML modes. Inventing a key
  path here would make `_dig` return a substring instead of the page, and a `TypedDict`
  would claim a dict the wrapper never returns. The wrapper stays `-> Any`, which is
  the correct type for a string-returning tool under this generator.

`probed_args` needed no scrubbing — every value is a public URL or a plain integer.

## Verification

The regenerated module parsed cleanly (`ast.parse` OK). `--embed-schema` attached
`fetch.__schema__` and an Args docstring carrying each parameter's description and
default. `run.py` was left to the harness verify stage, as instructed.
