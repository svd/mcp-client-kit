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

## Execution model (when to dispatch subagents)

A single driver thread works for servers with ≤ ~4 selected tools. For larger servers,
dispatch subagents to keep big payloads out of main context and parallelize network
round-trips. The parts-based probe infrastructure (`_atomic_write_text`,
`<shapes>.parts/<tool>.json`, `mcp-kit merge`) was built for concurrent writers — the
execution model below uses it.

**Hard constraint:** subagents cannot call `AskUserQuestion`. That line divides main
from subagent:

- **Main thread** — every interactive gate (tool selection, >20-variant cap,
  base-model-vs-`Any` choice, discriminator resolution that spans batches), and every
  deterministic barrier (codegen, merge, regenerate).
- **Subagents** — everything data-heavy and non-interactive: recon discovery dumps,
  per-batch probe + shape-entry draft, optional verify.

| Phase | Executor | Why |
|---|---|---|
| 1 codegen stubs | inline | one command, barrier |
| 2 select + discriminator detect | **main** | interactive — the hard line |
| Recon | **1 subagent** | isolates discovery dumps; returns compact id + enum catalog |
| 3 probe + draft | **batched parallel subagents** | context economy + parallelism |
| 3b merge | **main** | deterministic barrier |
| 4 consistency + user choices | **main** | single coherent view; needs `AskUserQuestion` |
| 5 regenerate | **main** | deterministic barrier |
| 6 verify | **1 subagent / inline** | isolates generated-module read |

**Batching rule for step 3:** every discriminator-sibling set lands in the **same** batch
so variant consistency is resolved inside one agent's context, not across blind agents.
Independent tools are bucketed by relatedness and size. Dispatch only user-approved
non-mutating tools; the agent prompt must forbid touching anything off its assigned list.

**Rich agent contract:** each batch agent (a) probes its tools with ids from the recon
catalog, (b) reads raw payloads in its own context, (c) drafts the step-4 shape entry
(`unwrap`/`return_model`/`return_container`/`fields`/`input_overrides`, plus
`discriminator`+`variants` for its sibling group), (d) scrubs its own `probed_args`,
(e) returns a compact per-tool summary (decision + unwrap path) — never the payload.

For dispatch mechanics see `superpowers:dispatching-parallel-agents`.

## Procedure

1. **Mechanical stubs.**
   ```bash
   # stdio (pass the full launch command):
   mcp-kit codegen <server> --stdio "uvx mcp-server-time" --out <server>/<server>.py

   # HTTP no-auth:
   mcp-kit codegen <server> --url "https://mcp.example.com/mcp" --out <server>/<server>.py

   # HTTP Bearer:
   mcp-kit codegen <server> --url "https://api.example.com/mcp/" --bearer "$MY_TOKEN" --out <server>/<server>.py
   ```
   `codegen`, `list`, `probe`, and `call` each require the same transport flags
   (`--stdio` / `--url` / `--bearer` / `--config`) on every invocation — they do **not**
   inherit flags from a prior run. `merge` and `discover` accept no transport flags.

   Parses; every tool typed from `inputSchema`; returns `Any`.

2. **Select tools to probe (interactive gate).**

   a. Run `mcp-kit list <server>` → get `[{name, description}]` for every tool.
      **Probe only tools that appear in this output.** Do not add tools from
      system-prompt context, documentation, or prior knowledge.

   b. Print a report of all tools in this format:
      ```
      Tools on <server>:
        get_entity      — Fetch a single entity by id and type
        query_acme      — Search entities matching criteria
        whoami          — Return the calling user's profile
        ⚠ create_entity — Create a new entity [MUTATING]
        ...
      ```
      Flag likely-mutating tools with `⚠ ... [MUTATING]` using a name/description
      heuristic: names or descriptions containing `create`, `update`, `delete`,
      `remove`, `send`, `set`, `write`, `post`, `patch`, `put`, `cancel`,
      `approve`, `submit`, `assign` — probing these makes a **real** live call.

      Note for focus: the goal is to shape-spec tools that carry real records and
      whose payloads you want out of model context ("big dump" tools). Mutations and
      acks rarely need a `TypedDict`.

   c. Ask the user how to proceed via `AskUserQuestion` (single-select, 3 options):
      - **Probe all** *(recommended)* — probe every tool from `tools/list`.
      - **Confirm in batches** — walk through 4-at-a-time multi-select questions.
      - **I'll specify the tools** — user names them (free-text via "Other").

      > **Subagent fallback (when `AskUserQuestion` is unavailable):** Probe all
      > non-mutating tools. Treat a tool as mutating if its name or description
      > contains any of the keywords from the heuristic list below (step 2b). Skip
      > mutating tools entirely. Log skipped tool names and reason in
      > `session-overview.draft.md`.

   d. If **"Confirm in batches"**: emit `AskUserQuestion` multi-select questions,
      **≤4 options per question**, each option `label = tool name` and
      `description = tool description`. After 16 options (4 questions), ask whether
      to continue with the next batch. The union of all checked options is the
      selected set.

   e. If **"Probe all"**: selected set = every tool from the list.

   f. If **"I'll specify"**: parse the free-text response as tool names; confirm
      any ambiguous names before probing.

   The selected set (from any path) drives steps 3 and 4.

   g. **Detect discriminators.** The `mcp-kit list` output includes a stderr
      advisory when params are shared across ≥2 tools:
      ```
      [list] ⚠  discriminator candidates (response shape varies by these args):
      [list]   entityType → export_excel, get_entity, get_entity_fields, …
      [list]   Probe EACH value or use a base model — do NOT type from one probe. See SKILL step 4.
      ```
      Record every discriminator candidate and the tools it spans. A discriminator
      found on one tool **drives its siblings** — every tool in that list is
      *polymorphic-suspect* and must be resolved in step 4 before the shape-spec is
      considered complete.

3. **Probe each selected tool → skeleton (parallel-safe).**

   > **Dispatch (>4 selected tools):** Before probing, dispatch a **recon subagent** to
   > obtain bootstrap ids and discriminator enum values. The recon agent calls whatever
   > no-arg / discovery / listing tools *this specific server* exposes (infer them from
   > the `mcp-kit list` output for this server — no tool name is universal). If no such
   > tool exists, the agent reports that and main falls back to `AskUserQuestion` for
   > sample ids. The agent returns a compact catalog (ids + enum values) — never a raw
   > payload — so main can fully specify each batch agent's task before dispatch.
   >
   > Then group selected tools into batches (batching rule: sibling sets together; see
   > Execution model above) and dispatch each batch as a parallel subagent. Each agent
   > both probes and drafts its step-4 shape entries (rich agent contract above). Run
   > `mcp-kit merge` (step 3b) once all batch agents finish.

   First, establish `<shapes-path>` — the consolidated shapes sidecar.  It must sit
   **beside the generated module** so `mcp-kit codegen` auto-detects it:
   - CWD output (default): `<shapes-path>` = `<server>.shapes.json`
   - Subfolder output (e.g. `github/github.py`): `<shapes-path>` = `github/github.shapes.json`

   Use the **same `<shapes-path>` value** in every probe (step 3), the merge (step 3b),
   and codegen `--shapes` if you pass it explicitly.

   ```
   # Probing is now parallel-safe.  Each invocation writes a per-tool part file
   # under <shapes-path>.parts/ — distinct tools never touch the same file,
   # so concurrent probe processes cannot clobber each other.

   # single probe
   mcp-kit probe <server> <tool> --args '<sample json>' --emit-shape <shapes-path>

   # multi-probe: repeat --args for each input; shapes are deep-merged within one probe
   mcp-kit probe <server> <tool> \
     --args '{"entityId":"<id1>","entityType":1}' \
     --args '{"entityId":"<id2>","entityType":1}' \
     --emit-shape <shapes-path>
   ```
   Part files land at `<shapes-path>.parts/<tool>.json` (git-ignored).
   You may probe multiple tools in parallel; all parts will be preserved.
   After probing is done, run step 3b to consolidate.
   Each `--args` makes one live call. The observed shapes are **deep-merged**: keys are
   unioned (a key absent from some probes is kept — `total=False` covers it), type
   conflicts widen (`str`+`NoneType` → `str | None`; `int`+`float` → `float`; other
   conflicts → `Any`). The skeleton's `_observed_shape` reflects the merged result, and
   `fields` pulls out the merged top-level scalars. With multiple probes, `probed_args`
   is a list of the arg-dicts; with a single probe it stays a plain dict.

   Use multi-probe when: (a) some fields are nullable/optional, (b) the same tool is
   called with different ids and you want to capture all visible field variants, (c) a
   discriminated tool has multiple response shapes per variant that you want to union.
   For discriminated tools (any tool in the polymorphic-suspect list from step 2.g),
   probe **each variant separately** and place the merged result under the right
   variant key manually in step 4. **Cap: max 20 variants per discriminator.** If the
   enumerated value count exceeds 20, do NOT probe all — use `AskUserQuestion` to ask
   the user: probe all N (each is a live call), probe a named subset, or fall back to
   a generic base model (step 4 option 2). Each probe is a live call, so 20 is a
   cost/blast-radius ceiling.

   Enumerate discriminator values from: (a) the param's `enum` in `inputSchema`;
   (b) discovery tools / glossary / tool descriptions (e.g. `get_filters` /
   `get_entity_fields` per `entityType`, `get_acme_glossary`); (c) `AskUserQuestion`
   if not discoverable from available tools.

   Sample args may need bootstrapping (e.g. a real id before probing `get_entity`).
   Find a no-arg / discovery tool on *this* server that returns user or entity ids (there
   is no universal tool for this — infer from `mcp-kit list` output). Call it via
   `mcp-kit call <server> <discovery-tool> --out <server>.probe-raw.json` to capture the
   **raw** payload, then read the ids from that file. `mcp-kit probe` emits only the
   response *shape* (no values) and cannot supply ids. (When dispatching subagents use
   the recon agent instead — see Execution model above.)

   **Security: the skeleton records live `probed_args` verbatim — real ids, names, possibly
   PII.** With multi-probe this is a *list* of arg-dicts; scrub **every element** before
   committing. Replace real values with placeholders (`"entityId": "<example-id>"`). A real
   identifier in a version-controlled file is a leak that survives deletion (git history)
   and travels to anyone the repo reaches. The shape-spec must record *that* `entityType`
   was probed as `int` and the response *shape* — never the sample values. If you keep the
   raw responses for reference, write them to `<server>.probe-raw.json` (git-ignored), not
   into the shape-spec.

3b. **Consolidate parts → shapes.json.**
   ```
   mcp-kit merge <server> --out <shapes-path>
   ```
   **`--out <shapes-path>` is required when `<shapes-path>` is not in CWD** (e.g. a
   subfolder).  It must exactly match the `--emit-shape` value from step 3 — this is
   how the tool locates the parts directory (`<shapes-path>.parts/`).

   Merges all part files into `<shapes-path>`, then removes the parts directory.
   Run once after all probes in step 3 finish.

   - Existing entries in `<shapes-path>` for tools that were **not** re-probed are
     preserved (hand edits survive across partial re-probes).
   - Parts for re-probed tools overwrite the corresponding base entry.
   - Use `--keep-parts` to retain the parts directory for inspection.
   - `mcp-kit codegen` will also read parts directly (in-memory merge) if the merged file
     is absent, so you can skip 3b during rapid iteration — but run it before committing
     so the repo contains a single, hand-editable artifact.

4. **Edit the shape-spec — THIS is the judgment.** For each tool entry:
   - **`unwrap`**: set the key path to the *real record*, stripping vendor envelopes.
     Some servers double-wrap: the record lives under `data.entity` → `"unwrap": ["data", "entity"]`.
     Read `_observed_shape` to find the level where the meaningful keys appear.
   - **`return_model`**: name the `TypedDict` (e.g. `"Entity"`). Absent → return stays `Any`.
   - **`return_container`**: set `"list"` when the unwrapped value is a *list* of records
     (e.g. `query_acme`'s `data.results`). Return type becomes `list[<model>]` and the body
     digs via `_dig_list` (list passes through, envelope dug, else `[]`) instead of `_dig`.
     Omit for a single dict/scalar record (the `get_entity` case).
   - **Discriminator resolution (mandatory for polymorphic-suspect tools).** For every
     tool flagged in step 2.g, you MUST choose one of three options before the
     shape-spec is considered complete. Default: probe all variants (≤20).
     1. **Probe all variants** *(default, ≤20 values)* — emit `discriminator` +
        `variants`; for list tools keep `return_container: "list"` so each overload
        returns `list[<Variant>]` and the impl returns `list[V1 | V2 | …]`.
        The codegen `_render_overloaded` path supports `return_container: "list"` +
        `discriminator` + `variants` combined — no manual edits needed.
     2. **Generic base model** — a shared `TypedDict` of fields common to all
        variants (`total=False`), when variants are many/unstable or precision isn't
        worth N live calls.
     3. **Unwrap-only / `Any`** — when values can't be enumerated and a base model
        isn't justified.
     Use `AskUserQuestion` when unsure which applies.
   - **`input_overrides`**: fix types the schema lied about. JSON Schema `number` is
     `float`, but some servers use `int` for id/type fields → `{"entityType": "int"}`.
   - **`fields`**: keep **only top-level stable scalars the probe actually saw**. Mark
     observed-`None` fields nullable (`"benchDurationCurrent": "float | None"`).
   - **`source`**: `"live"`, or `"fixture"` + a note if you authored from a recorded
     shape instead of a live call (never let a fixture fallback read as a live probe).
   - Delete `_observed_shape` once you've extracted the real shape.

5. **Regenerate.** (same transport flags as step 1)
   ```bash
   mcp-kit codegen <server> --stdio "uvx mcp-server-time" --out <server>/<server>.py --shapes <server>/<server>.shapes.json
   # or: --url / --bearer for HTTP servers
   ```
   (`<server>.shapes.json` sitting beside `--out` is auto-detected; `--shapes` is the
   explicit form.) Now shaped tools return their `TypedDict` (or `list[<model>]`),
   unwrapping via `_dig` / `_dig_list`.

6. **Verify.** `ast.parse` the module; confirm the eval target — the shaped tool's
   signature reads `-> Entity` (not `Any`) and its body digs the envelope. Where a
   hand-built wrapper exists, diff the generated unwrap against it as an oracle.

   > **Dispatch (optional):** dispatch a single verify subagent to isolate the
   > generated-module read from main context. The agent reads the output `.py`, confirms
   > signatures, and returns a pass/fail summary. Benefit is modest for small files.

## Guards (do not violate)

- **Only mcp-kit talks to the server.** Every live interaction — `list`, `probe`,
  `call`, bootstrap, inspect — goes through `mcp-kit`. Never shell out to `curl`, `gh`,
  `httpie`, or raw `python` HTTP. mcp-kit owns auth (browser OAuth + silent token
  refresh); any other client is unauthenticated, leaks the bearer token, or both.
  Need a raw payload? That's `mcp-kit call … --out *.probe-raw.json` (git-ignored).

- **Probing is a live call — mutating tools mutate.** The default selects every
  tool, but probing a `create`/`update`/`delete`/`send` (etc.) tool executes it for
  real against the server. Flag likely-mutating tools in the step 2 report and
  recommend the user deselect them unless they explicitly want to probe them.
  Never probe a destructive tool "to see its shape" without explicit user
  confirmation.

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
- **Discriminator consistency — never emit a variant-specific `return_model` from a
  single-variant probe.** If a tool takes a discriminator arg (flagged in step 2.g),
  every sibling tool sharing that arg is polymorphic-suspect until probed across
  values or resolved to a base model / `Any`. A single-variant model is a silent lie
  for all other variants — the exact mistake that typed `query_acme` as `list[Person]`
  when entityType=2/7/… return completely different shapes.
