---
name: generate-mcp-wrappers
description: Use when generating typed Python wrappers for an MCP server. Drives `mcpgen` to emit mechanical stubs, then applies live-probe findings (vendor-envelope unwrap, schema-lied types, nullability) via a shape-spec sidecar so the generated wrappers return typed records instead of opaque `Any`.
---

# Generate typed MCP wrappers (the judgment pass)

`mcpgen codegen` does the deterministic 80%: `tools/list` → one typed `async def`
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
`<shapes>.parts/<tool>.json`, `mcpgen merge`) was built for concurrent writers — the
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

> **When this skill itself runs as a subagent** (dispatched by a parent agent or workflow),
> execute as a **single driver thread — do NOT dispatch sub-subagents.** The recon + batched
> parallel probe model above is for main-thread execution only. All phases run inline.

**Batching rule for step 3:** every discriminator-sibling set lands in the **same** batch
so variant consistency is resolved inside one agent's context, not across blind agents.
Independent tools are bucketed by relatedness and size. Dispatch only user-approved
non-mutating tools; the agent prompt must forbid touching anything off its assigned list.

**Rich agent contract:** each batch agent (a) probes its tools with ids from the recon
catalog, (b) reads raw payloads in its own context, (c) drafts the step-4 shape entry
(`unwrap`/`return_model`/`return_container`/`fields`/`input_overrides`, plus
`discriminator`+`variants` for its sibling group), (d) writes the part with **raw**
`probed_args` (parts are gitignored; scrub runs post-merge at step 4),
(e) returns a compact per-tool summary (decision + unwrap path) — never the payload.

For dispatch mechanics see `superpowers:dispatching-parallel-agents`.

## Procedure

1. **Mechanical stubs.**

   `mcpgen` resolves a server **by name** from a config file — pointed to by the
   `MCPGEN_SERVERS` env var or the `--config` flag. This is the primary form: the bare
   `<server>` name maps to its transport and forwards the server's `env` block (API keys,
   etc.) to the launched process. `MCPGEN_SERVERS` must prefix the command in the **same
   shell invocation** — it does not persist across calls.
   ```bash
   MCPGEN_SERVERS=servers.json mcpgen codegen <server> --out <server>/<server>.py
   ```

   **Alternative — pass transport values directly, without a config file:**
   ```bash
   # stdio (pass the full launch command):
   mcpgen codegen <server> --stdio "uvx mcp-server-time" --out <server>/<server>.py

   # HTTP no-auth:
   mcpgen codegen <server> --url "https://mcp.example.com/mcp" --out <server>/<server>.py

   # HTTP Bearer:
   mcpgen codegen <server> --url "https://api.example.com/mcp/" --bearer "$MY_TOKEN" --out <server>/<server>.py
   ```
   `codegen`, `list`, `probe`, and `call` each require the same transport flags
   (`--stdio` / `--url` / `--bearer` / `--config`) on every invocation — they do **not**
   inherit flags from a prior run. `merge` and `discover` accept no transport flags.

   Parses; every tool typed from `inputSchema`; returns `Any`.

2. **Select tools to probe (interactive gate).**

   a. Run `mcpgen list <server>` → get `[{name, description}]` for every tool.
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
      > mutating tools entirely.

   d. If **"Confirm in batches"**: emit `AskUserQuestion` multi-select questions,
      **≤4 options per question**, each option `label = tool name` and
      `description = tool description`. After 16 options (4 questions), ask whether
      to continue with the next batch. The union of all checked options is the
      selected set.

   e. If **"Probe all"**: selected set = every tool from the list.

   f. If **"I'll specify"**: parse the free-text response as tool names; confirm
      any ambiguous names before probing.

   The selected set (from any path) drives steps 3 and 4.

   g. **Detect discriminators.** The `mcpgen list` output includes a stderr
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

      **Filter before probing — two-pass disqualification:**

      *Pass 1 — auto-disqualify without probing.* Drop any candidate whose name matches
      a known input-only pattern. No reasoning required; these are never response keys:
      - **Pagination / window:** `page`, `limit`, `perPage`, `per_page`, `head`, `tail`,
        `since`, `after`, `before`, `offset`, `cursor`, `maxResults`, `count`
      - **Sort / order:** `sort`, `order`, `direction`, `orderBy`, `order_by`
      - **Path / repo identity:** `path`, `filePath`, `file_path`, `repoPath`, `repo_path`,
        `projectPath`, `repoName`, `repositoryName`, `workspacePath`
      - **Spans all tools in the selected set** — a global context arg, not a shape switch.

      *Pass 2 — post-probe confirm.* For any candidate that survived Pass 1, confirm the
      field appears in the *response* payload of at least one probed call (i.e. it is a
      key in `_observed_shape`). A parameter that appears only in `inputSchema.properties`
      but never in any observed response dict is an *input* parameter, not a response
      discriminator — discard it regardless of how many tools share it.

3. **Probe each selected tool → skeleton (parallel-safe).**

   > **Dispatch (>4 selected tools):** Before probing, dispatch a **recon subagent** to
   > obtain bootstrap ids and discriminator enum values. The recon agent calls whatever
   > no-arg / discovery / listing tools *this specific server* exposes (infer them from
   > the `mcpgen list` output for this server — no tool name is universal). If no such
   > tool exists, the agent reports that and main falls back to `AskUserQuestion` for
   > sample ids. The agent returns a compact catalog (ids + enum values) — never a raw
   > payload — so main can fully specify each batch agent's task before dispatch.
   >
   > Then group selected tools into batches (batching rule: sibling sets together; see
   > Execution model above) and dispatch each batch as a parallel subagent. Each agent
   > both probes and drafts its step-4 shape entries (rich agent contract above). Run
   > `mcpgen merge` (step 3b) once all batch agents finish.

   First, establish `<shapes-path>` — the consolidated shapes sidecar.  It must sit
   **beside the generated module** so `mcpgen codegen` auto-detects it:
   - CWD output (default): `<shapes-path>` = `<server>.shapes.json`
   - Subfolder output (e.g. `github/github.py`): `<shapes-path>` = `github/github.shapes.json`

   Use the **same `<shapes-path>` value** in every probe (step 3), the merge (step 3b),
   and codegen `--shapes` if you pass it explicitly.

   ```
   # Probing is now parallel-safe.  Each invocation writes a per-tool part file
   # under <shapes-path>.parts/ — distinct tools never touch the same file,
   # so concurrent probe processes cannot clobber each other.

   # single probe
   mcpgen probe <server> <tool> --args '<sample json>' --emit-shape <shapes-path>

   # multi-probe: repeat --args for each input; shapes are deep-merged within one probe
   mcpgen probe <server> <tool> \
     --args '{"entityId":"<id1>","entityType":1}' \
     --args '{"entityId":"<id2>","entityType":1}' \
     --emit-shape <shapes-path>
   ```
   **Quota / rate-limit / auth errors during probing.** If a probe returns an HTTP
   429/401/403, a quota-exceeded message, a rate-limit string, or an auth/credentials
   error (detectable by phrases like `"quota exceeded"`, `"rate limit"`, `"try again
   later"`, `"unauthorized"`, `"forbidden"`, `"invalid api key"`, `"authentication"`,
   `"not authenticated"`):
   - Set `_observed_shape: "str"` — the error payload is a `str`, which is honest.
   - Leave `return_model: null`.
   - Note in `session-overview.md` that the shape is an error string, not a success
     payload; record whether the failure was a quota/rate-limit or an auth error, and
     note what credential (env var, API key) must be set before re-running to capture
     the real success shape.
   - Do **not** retry more than once.

   The generated `-> Any` return type is correct; callers must handle the error string
   at runtime. Do not probe again hoping for a different result.

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

   > **Subagent fallback (when `AskUserQuestion` is unavailable):** Fall back to a
   > generic base model of common fields (step 4 option 2); use unwrap-only `Any` if
   > no stable shared base exists. Do not probe all N variants.

   Enumerate discriminator values from: (a) the param's `enum` in `inputSchema`;
   (b) discovery tools / glossary / tool descriptions (e.g. `get_filters` /
   `get_entity_fields` per `entityType`, `get_acme_glossary`); (c) `AskUserQuestion`
   if not discoverable from available tools.

   **Check `inputSchema.required` before constructing probe args.** If the array is
   non-empty, never probe with `'{}'` — call the tool with minimal valid args on the first
   attempt. Invent realistic-looking but fake values for required string/ID args that carry
   no `enum`. For GitHub servers, prefer `owner: "microsoft", repo: "vscode"` as the default
   probe repo (`octocat/Hello-World` lacks releases/tags/issue fields and produces `[<empty>]`
   for those tools).

   **Inspect `inputSchema` for enum constraints before constructing probe args.** For each
   required param, check `inputSchema.properties[param].enum`. If an `enum` array is
   present, use its **first listed value** as the probe arg instead of inventing a value —
   invented values will be rejected with an MCP validation error. Record the chosen value
   in `probed_args`.

   Example: `"city": {"type": "string", "enum": ["New York", "Chicago", "Los Angeles"]}`
   → probe with `city="New York"`.

   **JSON-in-string detection.** Some servers serialize structured data as a JSON string
   inside the MCP envelope (e.g. `directory_tree` returns a JSON string, not a parsed
   dict). If `_observed_shape == "str"` and the raw probe value successfully parses with
   `json.loads()` into a dict or list, annotate the shape entry with `"_json_unwrap": true`
   and re-enter shape analysis on the parsed object — the tool may qualify for a `TypedDict`
   model after unwrapping. If `json.loads()` fails, `_observed_shape: "str"` stands.

   **Image / binary / media tools.** Tools returning MCP `image`, `audio`, or `blob`
   content (base64 + MIME type) surface as `_observed_shape: "str"` — the probe reads
   only the text envelope, so the structured binary record (`data` + `mimeType`) is
   invisible. If a tool description mentions "image", "media", "audio", "base64", or
   "binary", leave the wrapper as `-> Any`, note it in `session-overview.md`, and do
   not attempt to model the shape from a single probe.

   **Empty-store probes produce under-typed list fields.** If a read tool returns an
   empty list (`[]`), the inner element shape is unobservable. Do not fabricate a schema
   from zero samples — leave the field typed as `list`. Note in `session-overview.md`
   that the inner model is unobservable at probe time, and recommend re-running
   `mcpgen probe` after seeding the server with representative data (e.g. via
   `mcpgen call <mutating-tool>`) to capture inner field shapes.

   Sample args may need bootstrapping (e.g. a real id before probing `get_entity`).
   Find a no-arg / discovery tool on *this* server that returns user or entity ids (there
   is no universal tool for this — infer from `mcpgen list` output). Call it via
   `mcpgen call <server> <discovery-tool> --out <server>.probe-raw.json` to capture the
   **raw** payload, then read the ids from that file. `mcpgen probe` emits only the
   response *shape* (no values) and cannot supply ids. (When dispatching subagents use
   the recon agent instead — see Execution model above.)

   **Security: the skeleton records live `probed_args` verbatim — real ids, names, possibly
   PII.** With multi-probe this is a *list* of arg-dicts. Batch agents write parts with
   **raw** `probed_args` — the `.parts/` directory is gitignored, so raw args never enter
   version control at this stage. The single scrub pass runs post-merge on the main thread
   (step 4): open `shapes.json` and replace PII after `mcpgen merge` has written both the
   shapes file and its gitignored `<server>.verify.json` sidecar. A real identifier in a
   version-controlled file is a leak that survives deletion (git history) and travels to
   anyone the repo reaches.

   **Only replace values that match a PII pattern** — email addresses, UUIDs
   (`xxxxxxxx-xxxx-…`), long numeric IDs (8+ digits), auth tokens, personal names, or
   hostnames that could identify a user or system.

   **Do NOT replace functional values** — timezone names (`"UTC"`, `"America/New_York"`),
   generic table names (`"users"`, `"products"`), public repo owners/names, ISO timestamps,
   standard SQL queries, or any value that is not personally identifiable. Replacing these
   breaks the roundtrip verifier, which passes `probed_args` to the live server. The
   gitignored `<server>.verify.json` sidecar preserves the pre-scrub args so the verifier
   can still make a real live call even after `shapes.json` is scrubbed.

   When a value *must* be redacted, add `"probe_args_scrubbed": true` to the shape-spec
   entry. The roundtrip verifier checks the sidecar first; `probe_args_scrubbed` is only
   needed when the sidecar is absent or does not cover that tool.

   The shape-spec must record *that* `entityType` was probed as `int` and the response
   *shape* — never the sample values. If you keep raw responses for reference, write them
   to `<server>.probe-raw.json` (git-ignored), not into the shape-spec.

3b. **Consolidate parts → shapes.json.**
   ```
   mcpgen merge <server> --out <shapes-path>
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
   - `mcpgen codegen` will also read parts directly (in-memory merge) if the merged file
     is absent, so you can skip 3b during rapid iteration — but run it before committing
     so the repo contains a single, hand-editable artifact.
   - Also emits a gitignored `<server>.verify.json` beside `<shapes-path>` — a flat
     `{tool: probed_args}` map sourced from raw parts (pre-scrub), for use by the
     roundtrip verifier. Partial re-probes overlay existing sidecar entries.

4. **Edit the shape-spec — THIS is the judgment.**

   **First: scrub `probed_args`.** This is the single scrub point — batch agents do NOT
   scrub their parts. Open `shapes.json` and replace all real ids, emails, names, UUIDs,
   and other PII in every `probed_args` entry with `<example-*>` placeholders (follow the
   PII vs functional-value guidance in step 3). The gitignored `<server>.verify.json`
   sidecar already holds the original args for the roundtrip verifier, so scrubbing
   `shapes.json` does not break verification.

   Then, for each tool entry:
   - **`unwrap`**: set the key path to the *real record*, stripping vendor envelopes.
     Some servers double-wrap: the record lives under `data.entity` → `"unwrap": ["data", "entity"]`.
     Read `_observed_shape` to find the level where the meaningful keys appear.
   - **`return_model`**: name the `TypedDict` (e.g. `"Entity"`). Absent → return stays `Any`.
     Never set to a Python primitive name (`str`, `int`, `list`, etc.) — use `null` for tools that return plain scalars.
     The name must be a new, capitalized identifier (e.g. `CurrentTime`, `CommitSummary`) — never a Python keyword or builtin.
     When multiple tools share a conceptual type but differ in fields, mint distinct names:
     - singular read → base name (`Release`, `Issue`, `Commit`)
     - list endpoint → append `Summary` (`ReleaseSummary`, `CommitSummary`)
     - search endpoint → append the verb (`SearchIssueItem`, `SearchPRItem`)
     Two tools may not share a `return_model` name unless their `fields` dicts are identical. Check for collisions before finalising.
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

     > **Subagent fallback (when `AskUserQuestion` is unavailable):** Default to a
     > **generic base model** (option 2) — never emit a variant-specific `return_model`
     > from a single-variant probe alone. Fall back to unwrap-only `Any` only when
     > variants are too diverse or structurally incompatible.
   - **`input_overrides`**: fix types the schema lied about. JSON Schema `number` is
     `float`, but some servers use `int` for id/type fields → `{"entityType": "int"}`.
   - **`fields`**: keep **only top-level stable scalars the probe actually saw**. Mark
     observed-`None` fields nullable (`"benchDurationCurrent": "float | None"`).
   - **`source`**: `"live"`, or `"fixture"` + a note if you authored from a recorded
     shape instead of a live call (never let a fixture fallback read as a live probe).
   - Delete `_observed_shape` once you've extracted the real shape.

5. **Regenerate.** (same transport flags as step 1)
   ```bash
   mcpgen codegen <server> --stdio "uvx mcp-server-time" --out <server>/<server>.py --shapes <server>/<server>.shapes.json
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

7. **(Optional) Generate a smoke-test runner.**

   Once the wrappers are shaped and verified, ask the user whether to also generate a runner:

   > Use `AskUserQuestion` (single-select, 2 options):
   > - **Yes** — invoke `generate-mcp-runner` now
   > - **No / I'll do it later** — stop here
   >
   > **Subagent fallback (when `AskUserQuestion` is unavailable):** skip this step entirely
   > — do not invoke `generate-mcp-runner` automatically.

   If the user says yes, invoke `/generate-mcp-runner`. Pass the following details so the
   runner skill does not need to re-derive them:

   - **Server name** — `<server>` (the name used throughout this skill)
   - **Output folder** — the dir from `--out` (e.g. `<server>/`), which holds `<server>.py`,
     `<server>.shapes.json`, and `<server>.verify.json`
   - **Connection source** — exactly how step 1 reached the server:
     - config file: the `servers.json` path used via `MCPGEN_SERVERS=` / `--config`; **or**
     - direct params: `--stdio "<launch>"`, `--url "<url>"` (+ `--bearer "$ENV_VAR"` if applicable)
   - **Transport + auth kind** — the `(transport, auth_kind)` tuple, e.g. `(http, oauth)`,
     `(stdio, none)`, `(http, bearer)` — used by the runner to pick the right connection skeleton

   Example: *"generate runner for `acme`, output dir `acme/`, reached via
   `MCPGEN_SERVERS=servers.json` (http, oauth)"*

   That skill reads the module, `shapes.json`, and `verify.json` you produced and authors a
   workflow-ordered, shape-aware smoke test in one step. See `skills/generate-mcp-runner/SKILL.md`
   for the full procedure. This step is a pointer only — do not duplicate the runner procedure here.

## Guards (do not violate)

- **Only mcpgen talks to the server.** Every live interaction — `list`, `probe`,
  `call`, bootstrap, inspect — goes through `mcpgen`. Never shell out to `curl`, `gh`,
  `httpie`, or raw `python` HTTP. mcpgen owns auth (browser OAuth + silent token
  refresh); any other client is unauthenticated, leaks the bearer token, or both.
  Need a raw payload? That's `mcpgen call … --out *.probe-raw.json` (git-ignored).

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
- **Scrub `probed_args` before committing.** The post-merge scrub at step 4 is the single
  scrub point — placeholder any real ids/names/PII directly in `shapes.json`. Parts
  (`.parts/` dirs) and `<server>.verify.json` are gitignored raw counterparts; the only
  committable artifact is a fully-scrubbed `shapes.json`.
- **Drift is not the type's job.** A `TypedDict` catches no runtime drift by design.
  Schema drift is the deferred `--check` mode's job (re-probe → diff vs stored
  shape-spec), not a reason to pick a heavier return type.
- **Discriminator consistency — never emit a variant-specific `return_model` from a
  single-variant probe.** If a tool takes a discriminator arg (flagged in step 2.g),
  every sibling tool sharing that arg is polymorphic-suspect until probed across
  values or resolved to a base model / `Any`. A single-variant model is a silent lie
  for all other variants — the exact mistake that typed `query_acme` as `list[Person]`
  when entityType=2/7/… return completely different shapes.
