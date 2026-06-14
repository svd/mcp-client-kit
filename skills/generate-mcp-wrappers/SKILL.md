---
name: generate-mcp-wrappers
description: Use when generating typed Python wrappers for an MCP server. Drives `mcp-kit` to emit mechanical stubs, then applies live-probe findings (vendor-envelope unwrap, schema-lied types, nullability) via a shape-spec sidecar so the generated wrappers return typed records instead of opaque `Any`.
---

# Generate typed MCP wrappers (the judgment pass)

`mcp-kit codegen` does the deterministic 80%: `tools/list` → one typed `async def`
per tool against the `McpCaller` seam. It returns `Any` and is blind to vendor
response envelopes. **This skill is the 20% judgment that an LLM must do:** probe a
real call, read the actual response, and record what the input schema couldn't tell
you — into a **shape-spec sidecar** (`<server>.shapes.json`, data not code). Codegen
re-consumes that file to emit unwrap helpers + `TypedDict` return models. The split
keeps generation pure and re-runnable (and sets up `--check` drift later).

## Procedure

1. **Mechanical stubs.**
   ```
   mcp-kit codegen <server> --out <server>.py
   ```
   Parses; every tool typed from `inputSchema`; returns `Any`.

2. **Curate.** Pick the tools that matter for the caller's pipeline — especially the
   "big dump" tools whose payloads you want out of model context. Don't shape-spec all
   16 tools; spec the few that carry real records.

3. **Probe each chosen tool → skeleton.**
   ```
   mcp-kit probe <server> <tool> --args '<sample json>' --emit-shape <server>.shapes.json
   ```
   This makes one live call and writes a skeleton: top-level scalars become `fields`,
   `unwrap: []`, `source: "live"`, plus `_observed_shape` for your reference. Sample
   args may need bootstrapping (e.g. call a no-arg `whoami` first to get a real id).

   **Security: the skeleton records the live `probed_args` verbatim — real ids, names,
   possibly PII.** Before the shape-spec becomes committable, replace those values with
   placeholders (`"entityId": "<example-id>"`). A real identifier in a version-controlled
   file is a leak that survives deletion (git history) and travels to anyone the repo
   reaches. The shape-spec must record *that* `entityType` was probed as `int` and the
   response *shape* — never the sample values. If you keep the raw response for reference,
   write it to `<server>.probe-raw.json` (git-ignored), not into the shape-spec.

4. **Edit the shape-spec — THIS is the judgment.** For each tool entry:
   - **`unwrap`**: set the key path to the *real record*, stripping vendor envelopes.
     Radar double-wraps: the record lives under `data.entity` → `"unwrap": ["data", "entity"]`.
     Read `_observed_shape` to find the level where the meaningful keys appear.
   - **`return_model`**: name the `TypedDict` (e.g. `"Entity"`). Absent → return stays `Any`.
   - **`input_overrides`**: fix types the schema lied about. JSON Schema `number` is
     `float`, but radar id/type fields are `int` → `{"entityType": "int"}`.
   - **`fields`**: keep **only top-level stable scalars the probe actually saw**. Mark
     observed-`None` fields nullable (`"benchDurationCurrent": "float | None"`).
   - **`source`**: `"live"`, or `"fixture"` + a note if you authored from a recorded
     shape instead of a live call (never let a fixture fallback read as a live probe).
   - Delete `_observed_shape` once you've extracted the real shape.

5. **Regenerate.**
   ```
   mcp-kit codegen <server> --out <server>.py --shapes <server>.shapes.json
   ```
   (`<server>.shapes.json` sitting beside `--out` is auto-detected; `--shapes` is the
   explicit form.) Now shaped tools return their `TypedDict`, unwrapping via `_dig`.

6. **Verify.** `ast.parse` the module; confirm the eval target — the shaped tool's
   signature reads `-> Entity` (not `Any`) and its body digs the envelope. Where a
   hand-built wrapper exists, diff the generated unwrap against it as an oracle.

## Guards (do not violate)

- **The type is a hint, not validation.** A `TypedDict` from one probe is partial
  knowledge stated honestly. Don't reach for Pydantic to "enforce" it — a model built
  from one response rejects valid variant responses with false authority. Zero runtime
  cost + zero dependency is the point; generated wrappers stay importable anywhere
  (the seam principle).
- **Don't model depth from one probe.** Promote only the top 1–2 levels of stable
  scalars. Deep/variadic nests (`proposals.candidate.seniority.level`) stay `dict` /
  `Any`. Over-modelling = authoritative lies about a shape you saw once.
- **Scrub `probed_args` before committing.** Every probe captures live data; the
  shape-spec is committable input, so placeholder any real ids/names/PII. Generalizable
  rule, not a one-off — see step 3.
- **Drift is not the type's job.** A `TypedDict` catches no runtime drift by design.
  Schema drift is the deferred `--check` mode's job (re-probe → diff vs stored
  shape-spec), not a reason to pick a heavier return type.
