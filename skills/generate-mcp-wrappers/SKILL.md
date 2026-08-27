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
dispatch subagents to keep big payloads out of main context, and — against a local
`stdio` server — to parallelize network round-trips. Against a hosted HTTP server the
probe interval rules out fan-out, so subagents buy context economy only.

The parts-based probe infrastructure (`_atomic_write_text`, `<shapes>.parts/<tool>.json`,
`mcpgen merge`) supports concurrent writers — the execution model below uses it.

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
| Discriminator Pass 2 | **main** | few calls, decides the sweep's variant count |
| 3 probe + draft | **batched parallel subagents** (local `stdio`); one agent for hosted HTTP | context economy + parallelism, bounded by the probe interval |
| 3b merge | **main** | deterministic barrier |
| 4 consistency + user choices | **main** | single coherent view; needs `AskUserQuestion` |
| 5 regenerate | **main** | deterministic barrier |
| 6 verify | **1 subagent / inline** | isolates generated-module read |

> **When this skill itself runs as a subagent** (dispatched by a parent agent or workflow),
> execute as a **single driver thread — do NOT dispatch sub-subagents.** The recon + batched
> parallel probe model above is for main-thread execution only. All phases run inline.

**Batching rule for step 3:** every discriminator-sibling set lands in the **same** batch
so variant consistency is resolved inside one agent's context, not across blind agents.
Independent tools are bucketed by relatedness and size. Dispatch only the non-mutating
tools in the step-2 selected set; the agent prompt must forbid touching anything off its
assigned list. A mutating tool the user explicitly approved is probed inline on the main
thread, never handed to a batch agent.

**Rich agent contract:** each batch agent (a) probes its tools with ids from the recon
catalog, (b) reads raw payloads in its own context, (c) drafts the step-4 shape entry
(`unwrap`/`return_model`/`return_container`/`fields`/`input_overrides`, plus
`discriminator`+`variants` for its sibling group), (d) writes the part with **raw**
`probed_args` (parts are gitignored; scrub runs post-merge at step 4),
(e) returns a compact per-tool summary (decision + unwrap path) — never the payload.

For dispatch mechanics see `superpowers:dispatching-parallel-agents`.

## Procedure

0. **Resolve the CLI.** Requires `mcpgen >= 0.3.0`. `list --schema`,
   `codegen --embed-schema`, and enum `Literal` arrive at 0.2.0; 0.3.0 is the floor
   because it is where a **string-valued** discriminator works — before it, the overload
   renderer coerces every variant key with `int()`, so step 4's option 1 on a `string`
   discriminator (which step 2.e admits) dies in step 5 with `ValueError: invalid
   literal for int()` after the whole probe sweep has been paid for. The command is often
   not on `PATH`: in a `uv`-managed project it lives inside the venv. Probe the three
   forms and keep the first that both answers **and** meets the floor — an outdated
   `mcpgen` on `PATH` must not shadow a current one in the venv:

   ```bash
   min=0.3.0; MCPGEN=
   for c in "mcpgen" "uv run mcpgen" ".venv/bin/mcpgen"; do
     out=$(eval "$c --version" 2>/dev/null) || continue
     ver=$(printf '%s\n' "$out" | awk '{print $2}')
     printf '%s' "$ver" | grep -Eq '^[0-9]+(\.[0-9]+)+' || continue
     [ "$(printf '%s\n%s\n' "$min" "$ver" | sort -V | head -n 1)" = "$min" ] || \
       { echo "skipping $c ($ver < $min)"; continue; }
     MCPGEN="$c"; break
   done
   [ -n "$MCPGEN" ] || { echo "no mcpgen >= $min found on any invocation"; exit 1; }
   echo "resolved: $MCPGEN ($ver)"
   ```

   `eval` is required because a bare `$c` does not word-split in `zsh`, so the two-word
   `uv run mcpgen` form would never match. `MCPGEN=` clears any inherited value, so a
   run where nothing qualifies fails instead of echoing a stale one. A candidate counts
   only when `--version` exits zero **and** its second word opens with a dotted numeric release — a
   wrapper that prints a diagnostic and fails is otherwise read as a version. Shell variables do not survive between shell
   invocations, so `$MCPGEN` is not available to later steps: state the resolved
   invocation once in your report, then substitute that literal string wherever a
   command block below writes `<mcpgen>`. An environment prefix composes with every form —
   `MCPGEN_SERVERS=servers.json uv run mcpgen …` passes the variable through.

1. **Mechanical stubs.**

   `mcpgen` resolves a server **by name** from a config file — pointed to by the
   `MCPGEN_SERVERS` env var or the `--config` flag. This is the primary form: the bare
   `<server>` name maps to its transport and forwards the server's `env` block (API keys,
   etc.) to the launched process. `MCPGEN_SERVERS` must prefix the command in the **same
   shell invocation** — it does not persist across calls.
   ```bash
   mkdir -p "<server>"
   MCPGEN_SERVERS=servers.json <mcpgen> codegen <server> --out <server>/<server>.py --embed-schema
   ```

   **Alternative — pass transport values directly, without a config file.** Swap the
   transport flag per variant; the `--out … --embed-schema` tail is identical in each
   (written out because an unquoted `$TAIL` would not word-split in `zsh`):
   ```bash
   <mcpgen> codegen <server> --stdio "uvx mcp-server-time" --out <server>/<server>.py --embed-schema
   <mcpgen> codegen <server> --url "https://mcp.example.com/mcp" --out <server>/<server>.py --embed-schema
   <mcpgen> codegen <server> --url "https://api.example.com/mcp/" --bearer "$MY_TOKEN" --out <server>/<server>.py --embed-schema
   ```
   `codegen`, `list`, `probe`, and `call` each require the same transport flags
   (`--stdio` / `--url` / `--bearer` / `--config`) on every invocation — they do **not**
   inherit flags from a prior run. `merge` and `discover` accept no transport flags.

   `codegen`, `list`, `probe`, and `call` each require the same transport flags
   (`--stdio` / `--url` / `--bearer` / `--env` / `--config`) on every invocation — they do **not**
   inherit flags from a prior run. `merge` and `discover` need no connection flags
   (`merge` accepts `--config` and ignores it).
   Later command blocks elide those flags for brevity: add the same transport form this
   step used — including the `MCPGEN_SERVERS=` prefix — to every runnable `codegen`,
   `list`, `probe`, and `call` line below. `merge` lines take none.

   A server name is a config key or a URL, so it can hold spaces, `?`, `&`, or glob
   characters. When you substitute `<server>` and the paths derived from it, **quote the
   substituted value** (`"my server"`, `"my server/my server.py"`); an unquoted one word-
   splits or globs in `sh` and `bash`, and globs in `zsh`.

   Parses; every tool typed from `inputSchema`; returns `Any` until a shape-spec exists
   beside the output (step 5 regenerates once it does). `--embed-schema` (used
   above) also emits `fn.__schema__ = {<raw inputSchema>}` on each function and an Args
   docstring section listing each param's description, enum values, and default.

2. **Select tools to probe (interactive gate).**

   Keep a running `session-overview.md` beside the generated module (the `--out` dir).
   It is the human-readable log for everything the shape-spec cannot hold: skipped
   mutating tools, unprobed tools and why, and discriminator verdicts.

   a. Run `<mcpgen> list <server> --schema` → get `[{name, description, inputSchema}]`
      for every tool, plus `annotations` on the tools whose server supplies it — the key
      is absent when it does not. **Probe only tools that appear in this output.** Do not
      add tools from system-prompt context, documentation, or prior knowledge.
      The `inputSchema` field in this output is the authoritative source for step 3's
      required-arg and enum-constraint checks — no separate schema fetch is needed.

   b. Print a report of all tools in this format:
      ```
      Tools on <server>:
        get_entity      — Fetch a single entity by id and type
        query_acme      — Search entities matching criteria
        whoami          — Return the calling user's profile
        ⚠ create_entity — Create a new entity [MUTATING]
        ...
      ```
      Flag likely-mutating tools with `⚠ ... [MUTATING]` using the **fallback order**
      below. Both items use one shared test, defined here so the two cannot drift:

      > **The keyword test.** Words: `create`, `update`, `delete`, `remove`, `send`,
      > `set`, `write`, `post`, `patch`, `put`, `cancel`, `approve`, `submit`, `assign`,
      > `add`, `append`, `insert`, `upsert`, `push`, `close`, `revoke`.
      >
      > Match the **name only, never the description** — prose routinely names mutating
      > verbs while describing a read ("returns recently created records"). Split the
      > name on `_`, `-`, and camelCase boundaries and compare **whole words**, so
      > `created` does not match `create` and `get_address` does not match `add`. The
      > test **passes** (the tool looks mutating) when any whole word is on the list.
      >
      > **Read-verb exemption — the one judgment call.** When a read verb (`get`, `list`,
      > `read`, `search`, `find`, `fetch`, `query`, `describe`, `show`, `count`) leads
      > the name — after any server or object prefix, since `board_list_items` and
      > `sharepoint_folder_search` are the same shape as `list_items` — ask what the
      > matched word is doing in the name.
      > Naming *what is read* does not make the tool write it — `list_add_ons`,
      > `get_close_reasons`, `search_push_events` are reads whose match is a noun.
      > Naming a *second action* does — `get_or_create_entity`, `find_or_create_page`,
      > `getOrCreateThread` each really can write. Exempt the first kind; flag the
      > second. This is the only place in the test where you reason rather than match,
      > so when the reading is genuinely unclear, flag it — an over-flagged tool costs
      > one line in `mutating-skipped`, an under-flagged one gets called for real.

      1. **Primary — `annotations.readOnlyHint`.** If a tool's `annotations` object
         (from step 2a's `mcpgen list --schema` output) has `readOnlyHint`, trust it:
         `readOnlyHint: true` → safe to probe; `readOnlyHint: false` → mutating. Do not
         re-litigate an agreeing hint with
         the keyword test above — that over-flags read-only tools and suppresses
         probing.

         **Contradiction check — the one time you do look twice.** Trust the hint unless
         the server's own metadata or the tool's name argues against it. Run both tests
         on every `readOnlyHint: true` tool; neither costs a live call:
         - **Self-contradicting annotations** — `destructiveHint: true` or
           `idempotentHint: false` alongside `readOnlyHint: true`. The server contradicts
           itself. (`list --schema` passes through every annotation field the server
           sets, so these are already in hand.)
         - **Name contradicts hint** — run the keyword test above. The hint is disputed
           when it passes; otherwise the hint stands. `create_item`, `user_delete` and
           `get_or_create_entity` are disputed; `list_recently_created_items` and
           `getDeletedRecords` are not.

         On either hit, do **not** silently probe and do **not** silently skip: treat the
         tool as mutating, flag it `⚠ ... [MUTATING — hint disputed]` in the report, and
         record the reason `readOnlyHint: true contradicted by <what>` — the recording
         rule below covers this path and says where it lands. It becomes
         probeable only on an explicit user yes, exactly like any other mutating tool.
         An agreeing hint stays trusted and needs no marker.
      2. **Fallback — keyword heuristic + semantic read.** Only when `annotations` is
         absent or lacks `readOnlyHint` (common — many servers don't set it). Run the
         keyword test above; a pass flags the tool. Its whole-word, name-only matching
         is what keeps `get_address` and `list_closed_issues` unflagged, and that
         matters in both directions: step 2c skips every flagged tool, so a false flag
         drops coverage — the mirror of the miss the list exists to prevent.
         **Then** read the description semantically for mutating verbs no keyword
         catches (e.g. "records changes", "switches branches", "writes a memo"). The
         test is a last resort, not a sufficient safety net on its own — probing makes a
         **real** live call, so when in doubt, treat as mutating.

         **Record the verdict, do not resolve it silently.** This applies to any tool
         flagged as mutating without a `readOnlyHint: false` to say so — by this
         fallback, or by 2.b.1's contradiction check. A tool whose annotations carry no
         hint has no third route: it is this fallback's population, and the keyword test
         is what decides it. A tool cleared by
         `readOnlyHint: true` needs no marker.

         - **Probed** — it carries `"_mutating_suspect": true` and a one-line
           `"_mutating_reason"` in its shape-spec entry:
           `{"_mutating_suspect": true, "_mutating_reason": "name contains 'append'; no readOnlyHint"}`.
           Add both **in step 4, after `mcpgen merge`**. Merge replaces a re-probed
           tool's entry wholesale rather than unioning its keys, so anything hand-added
           beforehand is dropped without a word — and must be re-added after any later
           partial re-probe.
         - **Skipped** — there is no shape entry to hold it. Record it under a
           `## mutating-skipped` heading in `session-overview.md` with that same
           one-line reason.

         Either way the verdict survives the run.

      Note for focus: the goal is to shape-spec tools that carry real records and
      whose payloads you want out of model context ("big dump" tools). Mutations and
      acks rarely need a `TypedDict`, so the default set below may be pruned to the
      record-carrying tools — say in `session-overview.md` what you dropped and why.
      Prune before sizing the run: the Execution model picks single-driver vs fan-out
      from how many tools are *selected*, so the prune has to come first.

   c. Ask the user how to proceed via `AskUserQuestion` (single-select, 3 options):
      - **Probe all** *(recommended)* — probe every tool from `tools/list`.
      - **Confirm in batches** — walk through 4-at-a-time multi-select questions.
      - **I'll specify the tools** — user names them (free-text via "Other").

      > **Subagent fallback (when `AskUserQuestion` is unavailable):** Probe all
      > non-mutating tools, using the same fallback order as step 2b (`readOnlyHint`
      > first, then the keyword list + semantic read). Skip mutating tools entirely.

   d. If **"Confirm in batches"**: emit `AskUserQuestion` multi-select questions,
      **≤4 options per question**, each option `label = tool name` and
      `description = tool description`. After 16 options (4 questions), ask whether
      to continue with the next batch. The union of all checked options is the
      selected set.

   e. If **"Probe all"**: selected set = every tool from the list.

   f. If **"I'll specify"**: parse the free-text response as tool names; confirm
      any ambiguous names before probing.

   The selected set (from any path) drives steps 3 and 4.

   e. **Detect discriminators.** The advisory is stderr of the *same* `list --schema`
      run step 2.a already made, so the answer is in hand before any analysis.

      *Precondition — use it to tell a genuinely absent advisory from an unread one.*
      A candidate exists only where two or more tools declare a parameter under the
      **same name, same case**, carrying a top-level `"type"` of exactly `"integer"`,
      `"number"`, or `"string"`, and absent from the engine's own denylist — the
      lowercased-exact-name list in Pass 1's first sentence, **not** the camelCase
      additions Pass 1 layers on top of it. A union such as `["string", "null"]`, or a
      scalar expressed only through `anyOf`/`oneOf`, does not clear the type test. Where
      nothing clears all four, no advisory can fire — record `discriminators: N/A` in
      `session-overview.md` and move to step 3. Two tools sharing only `page`, only a
      boolean or object param, or `entityType` against `entitytype` all fail it;
      `maxResults` and `filePath` **pass** it, because the engine does not drop those.
      Otherwise the advisory names what survived:
      ```
      [list] ⚠  discriminator candidates (response shape varies by these args):
      [list]   entityType → export_excel, get_entity, get_entity_fields, …
      [list]   Probe EACH value or use a base model — do NOT type from one probe. See SKILL step 4.
      ```
      Record every discriminator candidate and the tools it spans. A discriminator
      found on one tool **drives its siblings** — every tool in that list is
      *polymorphic-suspect*, and stays so through Pass 2 — which can confirm a candidate
      but never disprove one. An **inconclusive** Pass 2 does not clear it: the tools
      remain polymorphic-suspect and step 4 must still resolve them. That is what "flagged
      in step 2.e" means
      wherever the rest of this file says it. Resolution is mandatory for siblings
      **inside the selected set**; a sibling outside it is recorded as unresolved, never
      probed to close the gap.

      **Filter before probing — two-pass disqualification:**

      *Pass 1 — auto-disqualify by name.* The advisory is already pre-filtered: `list`
      drops `page`/`per_page`/`limit`/`offset`/`cursor`/`path`/`repo`/`owner`/`org`/
      `branch`/`ref`/`method`/`query`/`search`/`filter`/`sort`/`order`/`direction`/
      `context_lines`/`include`/`exclude` and exactly five identity forms —
      `reponame`, `repo_name`, `repositoryname`, `username`, `orgname` — matching
      lowercased exact names. That list is the whole of it: any other `*name` param
      (`fileName`, `entityName`) reaches you. Do not re-check the ones it drops. Pass 1
      catches the camelCase and alternate spellings
      that exact match misses — no reasoning required; these steer a query, never the
      shape:
      - **Pagination / window:** `perPage`, `head`, `tail`, `since`, `after`, `before`,
        `maxResults`, `count`
      - **Sort / order:** `orderBy`, `order_by`
      - **Path identity:** `filePath`, `file_path`, `repoPath`, `repo_path`,
        `projectPath`, `workspacePath`

      Breadth is **not** a disqualifier. A parameter every selected tool accepts can still
      be a real discriminator — on some servers every tool takes `entityType`. Judge a
      candidate by what it does to the response, never by how many tools share it.

      *Pass 2 — confirm by comparing response shapes.* This pass makes live calls, so it
      runs at the **start of step 3**, after the ignore preflight and once `<shapes-path>`
      exists — not here. Record the surviving candidates now; confirm them there.

      For each candidate that survived Pass 1, pick one **non-mutating** tool that takes
      it and probe **three distinct values** of that parameter — or every value, if its
      `enum` has three or fewer — holding every other argument fixed.

      **Normalize before comparing.** `_observed_shape` renders a list of more than one
      element as `[<element shape>, "...xN"]`, where `N` is that response's element
      count. `N` tracks how much data came back, not the shape, so two identical shapes
      differ textually whenever the counts differ. Ignore every `...xN` sentinel when
      comparing; a differing `N` alone is **not** a shape difference.

      Then judge:
      - **Any two values differ** (a key present in one and absent in the other, or the
        same key with a different type) → confirmed discriminator. Resolve it per step 4.
        Stop probing this candidate; one difference is proof.
      - **All probed values identical** → **inconclusive, not disproven.** Three samples
        of one tool cannot clear the parameter for its siblings: a fourth value, or the
        same value on a different sibling, may still switch shape. Stop probing the
        candidate here, but it stays polymorphic-suspect: resolve it in step 4 like any
        other flagged tool — option 1 where the variants can be enumerated and probed,
        otherwise option 2 or 3 — and record it in `session-overview.md` as unconfirmed
        — with the tool and the exact values compared — typing the affected returns no more
        precisely than
        the probes justify. Never let an inconclusive result promote a
        single-variant model to a confident one.

      Do **not** require the parameter to appear as a key in the response. A server can
      switch shape on an argument it never echoes back — `entityType=1` may return plain
      `Person` fields with no `entityType` key — so absence from the payload proves
      nothing either way.

      This costs up to three live calls per surviving candidate. That is the price of the
      answer: guessing from names alone is what produces a confident `list[Person]` for a
      tool that returns something else at `entityType=7`. The Pass 1 list keeps the
      candidate set small, and the 20-variant threshold below still caps full resolution.
      Get the values from the param's `enum`, or from a discovery tool, as below. Issue
      the values as **separate `probe` invocations**, never as repeated `--args` in one:
      a single probe deep-merges all its calls into one `_observed_shape`, which unions
      away the very difference this pass is looking for. Each probe overwrites the tool's
      part file — read the shape between calls, or only the last one survives.

3. **Probe each selected tool → skeleton (parallel-safe).**

   First, establish `<shapes-path>` — the consolidated shapes sidecar.  It must sit
   **beside the generated module** so `mcpgen codegen` auto-detects it:
   - CWD output (default): `<shapes-path>` = `<server>.shapes.json`
   - Subfolder output (e.g. `github/github.py`): `<shapes-path>` = `github/github.shapes.json`

   Use the **same `<shapes-path>` value** in every probe (step 3), the merge (step 3b),
   and codegen `--shapes` if you pass it explicitly.

   > **Recon (>4 selected tools):** Only now, with the ignore preflight green and
   > `<shapes-path>` fixed, dispatch a **recon subagent** for bootstrap ids and
   > discriminator enum values — it makes live calls, so it must not run before the
   > preflight. It calls whatever no-arg / discovery / listing tools *this* server
   > exposes (infer them from `mcpgen list` — no tool name is universal), or reports that
   > none exist. Main then falls back to `AskUserQuestion` for sample ids where 2d's
   > condition holds; where it does not, probe only the tools whose required args
   > reference nothing and record the rest as unprobed, naming the id you lacked. It
   > returns a compact catalog, never a raw payload.

   **Then run step 2.e's Pass 2** — the variant probes per surviving discriminator
   candidate, comparing response shapes — before the main probe sweep. Skip it outright
   when step 2.e recorded `discriminators: N/A`; there is no candidate to confirm. It runs *after*
   recon because a candidate whose values are not declared in its `enum` can only get
   them from the recon catalog. Pass 2's verdicts — confirmed **or inconclusive**, per
   step 2.e — decide which tools stay polymorphic-suspect, and that decides how many
   variants the sweep below has to cover.

   > **Batch dispatch (>4 selected tools, local `stdio` only):** With Pass 2 settled,
   > group selected tools into batches (batching rule: sibling sets together; see
   > Execution model above) and dispatch each batch as a parallel subagent. **Against a
   > hosted HTTP server, do not fan out** — parallel agents cannot hold the ≥ 2 s probe
   > interval below, so probe from one agent at a time. Each agent
   > both probes and drafts its step-4 shape entries (rich agent contract above). Pass
   > every agent the same `<shapes-path>`. Run `mcpgen merge` (step 3b) once all batch
   > agents finish — `probe` only writes a per-tool part file; nothing consolidates
   > `<shapes-path>` until that merge.

   ```
   # Probing is parallel-safe.  Each invocation writes a per-tool part file
   # under <shapes-path>.parts/ — distinct tools get distinct files (the one
   # exception is the case-only collision noted below), so concurrent probe
   # processes cannot clobber each other.

   # single probe
   <mcpgen> probe <server> <tool> --args '<sample json>' --emit-shape <shapes-path>

   # multi-probe: repeat --args for each input; shapes are deep-merged within one probe
   <mcpgen> probe <server> <tool> \
     --args '{"entityId":"<id1>","entityType":1}' \
     --args '{"entityId":"<id2>","entityType":1}' \
     --emit-shape <shapes-path>
   ```

   **Probes for distinct tools are independent — issue them in one batch.** Each tool
   writes its own part file, so nothing serializes them. Put every probe in a single
   shell invocation (one `<mcpgen> probe` per line); do not spend one turn per tool.
   Against a local `stdio` server they may also go out as parallel tool calls — against
   a hosted server keep them sequential in one invocation and interleave `sleep 2`, per
   the pacing rule below. Each line still carries that tool's own valid args, built per
   the `inputSchema.required` rule below. The step-3b read afterwards is **one file
   read** of the merged `<shapes-path>` — not one read per tool.

   ```bash
   set -e   # fail-fast: without it the batch reports only the last probe's status
   <mcpgen> probe <server> tool_a --args '<args-for-tool_a>' --emit-shape <shapes-path>
   sleep 2  # hosted servers only; drop it for local stdio
   <mcpgen> probe <server> tool_b --args '<args-for-tool_b>' --emit-shape <shapes-path>
   ```

   Newline-separated commands return the *last* exit status, so a failing probe followed
   by a passing one reports success. `set -e` fixes the status but stops the batch at the
   first failure — the tools after it go unprobed and must be re-issued. Drop `set -e`
   and check each status instead when you would rather collect every result in one pass.
   Either way, label the check with the tool name yourself, and keep the failure — a bare
   `|| echo` returns 0 and hides it, from `set -e` too. Accumulate as the ignore preflight
   does: `… || { echo "FAILED tool_a"; bad=1; }` per line, `[ "$bad" -eq 0 ] || exit 1` at
   the end. Labelling is on you because `probe` announces the tool before calling it, but
   an unparseable `--args` payload fails ahead of that line and names nothing.

   Part filenames come from the tool name with its case preserved, so on a
   case-insensitive filesystem (macOS's default) two tools differing only in case share
   one part file and the second overwrites the first. It is rare, and reading between the
   two probes does not save the first — `merge` only ever sees the surviving part. Probe
   one, run step 3b, then probe the other and merge again; each merge folds its result
   into `<shapes-path>` before the next probe can overwrite the part.

   **Read the raw payload before classifying any failure — when there is one.** `probe`
   records structure, not content: a text payload collapses to the bare shape `"str"` and
   the words are gone. The error phrases below match against text `probe` never kept, so
   whenever a probe yields `"str"` or an error-shaped object, capture the payload before
   judging it:

   ```bash
   <mcpgen> call <server> <tool> --args '<same args>' --out <server>.<tool>.probe-raw.json
   ```

   A probe that writes no part file has nothing to capture — whether it died with a
   traceback or exited on a single `[…] error:` line; `call` fails the same way and
   writes no `--out` file either. Classify those from the error text alone: the next two
   blocks say how.

   **Quota / rate-limit / auth errors during probing.** An HTTP 429 arrives as a status
   with no body, so it is recognized from the error line, not a payload. Otherwise: if
   the captured payload carries a JSON `"error"` key, or is text that is *itself* an
   error message (the whole text is the failure, not prose that mentions one) — phrases
   like `"quota exceeded"`, `"rate limit"`, `"try again later"`, `"unauthorized"`,
   `"forbidden"`, `"invalid api key"`, `"not authenticated"` — treat it as a probe
   failure. Require the phrase to be the error itself (status code, error-shaped JSON,
   or the entire response is the complaint), not a bare substring match anywhere in a
   successful payload: a library description that happens to contain the word
   "authentication" is not an auth error — read the surrounding content before
   concluding a probe failed.

   When a probe genuinely fails this way, the two cases are recorded differently:
   - **The call returned, carrying an error payload.** Record the shape it actually
     has: a bare error string observes as `"str"`, while an error-shaped JSON object is
     parsed by the client and observes as a dict. Either way leave `return_model: null`
     — the generated `-> Any` is correct and callers handle the error at runtime.
   - **The transport or protocol failed.** There is no payload and therefore no shape:
     record the tool as **unprobed** with the reason. Do not invent
     `_observed_shape: "str"` for a call that never returned. Three markers put a failure
     here rather than in the challenge class below:
     - an `httpx.HTTPStatusError` reporting 429 **on the tool call**. A 429 quoted
       inside an `[mcpgen] error:` credential line is a throttled token refresh, not
       this — the credential rule below governs it;
     - any `[mcpgen] error:` line naming a credential or token endpoint. Most mention
       `mcpgen login` (in either case) after wording that varies — `OAuth re-auth
       needed for …`, `Token refresh failed (400, invalid_grant) …`, `No refresh_token
       for …` —
       and some carry no such tail at all, so match on the subject, not the sentence.
       These are **server-wide**: they are raised in the OAuth pre-flight, before any
       tool call, so every remaining tool fails identically and there is no point
       probing on. Two of them are transient rather than permanent — a line saying
       `retry later` or `retry when the authorization server is back` means the token
       endpoint was reachable but unhelpful. Wait once (the `Retry-After` if one is
       named, else 60 s) and re-issue the server's probing **from the top**; if it
       fails the same way, stop and record every unprobed tool with the credential note.
       Anything else here is permanent: stop now and record them the same way;
     - an `httpx.HTTPStatusError` reporting **401**. Only the OAuth transport intercepts
       401 and turns it into the message above; `--bearer`, static-header, and raw-URL
       transports install no OAuth provider, so there a stale token surfaces as a bare
       401 — the commonest static-credential expiry.
   - Either way, note in `session-overview.md` which case it was, whether the failure
     was a quota/rate-limit or an auth error, and what credential (env var, API key)
     must be set before re-running to capture the real success shape.
   - Do **not** retry at all — the transient-credential case above excepted, and unlike
     a challenge response (next paragraph), a quota or auth failure does not clear on a
     backoff. A 403 or 503 is **not** classed here:
     httpx builds its error message from the status, reason and URL alone, so a genuine
     permission denial and a bot challenge are indistinguishable at that point — the
     next block claims both and retries.

   **Challenge / interstitial responses — retryable, unlike the above.** A bot-protection
   interstitial is neither a shape nor a permanent failure. The tell is what the
   *exception* looks like, not the body: a challenge page never reaches you, because
   `probe` and `call` let httpx and SDK errors raise raw rather than excerpting the
   response. Read it as a challenge when any of these lands on a host that answered
   moments earlier:
   - an `httpx.HTTPStatusError` reporting 403, or any 5xx. Its message carries the status,
     reason and URL and never the body, so treat every one of these as a challenge
     first — an error payload the *tool call itself* returned is the auth failure above,
     and that one arrives as a payload with no HTTP status attached;
   - an `McpError` reporting `Connection closed`;
   - the probe does not return after the last `[probe]   [i/n] args=…` line — with or
     without an `Unexpected content type: text/html` line from the SDK. An
     interstitial served at HTTP 200 stalls the handshake indefinitely: the SDK pushes
     that error onto a stream nothing reads, so the probe hangs instead of exiting
     non-zero. Log line and stall are one marker, not two. `probe` has no timeout of
     its own — bound
     hosted probes with the harness's own timeout (never a `timeout` binary — see the
     Guards) so this surfaces as a stall rather than a hang.

   Any other transport-level exception — `httpx.ConnectError`, `ConnectTimeout`,
   `ReadTimeout`, `RemoteProtocolError` — or an `HTTPStatusError` on a status no rule
   above claims, belongs here too: retry once before recording the tool unprobed, since
   a connect blip must not permanently abandon a tool.

   A `[probe] error:` or `[call] error:` line that names no credential is neither class:
   `MCP tool result has empty content`, `Unknown server …`, `config not found: …` and
   their like are settled facts about this call, not a host under load. Record the tool
   unprobed with the message and do not retry — no backoff changes any of them.

   Do **not** record `_observed_shape` from a challenge. Retry that one tool with a fixed
   backoff — 60 s, then 120 s, then stop — and if the third attempt still challenges,
   record the tool as unprobed with the reason and move on. Once one tool has burned both
   retries on a given **marker** — a status, a `Connection closed`, a stall — stop
   retrying that marker for this server: what survives the backoff is not a challenge. A
   403 that does is a real permission denial; a 5xx, a `Connection closed`, or a stall
   that does is a host that is down or hanging. Keep probing the other tools — entitlement
   is usually per tool, so most answer through a persistent 403 — and record any *further*
   tool hitting that marker as unprobed, naming both candidate causes and saying in
   `session-overview.md` which credential would have to be set — the actionable half if
   the cause is entitlement, and lost if the failure is filed as a challenge.

   Retry as a **separate re-issue after the batch**, never inside it: the batch form
   aborts at the first failure under `set -e`, and its accumulator variant collects
   failures without
   retrying. Once a server has challenged once, widen the interval for the probes **not yet
   issued** to ~10 s — under the accumulator form the batch has already
   run to completion, so that means the retries and any later batch. Resuming the 2 s
   cadence that tripped it re-trips it on the next
   tool.

   **Pacing for hosted endpoints.** Against an HTTP/remote server, leave **≥ 2 s between
   live probes**. The multi-`--args` form fires its calls back-to-back inside a single
   invocation, so against a hosted server split a tool's variant probes into separate,
   paced invocations — reading the part file between them, per the overwrite rule below.
   Parallel batch subagents are a local-`stdio` optimization — against
   a hosted server probe from one agent at a time, or the interval cannot hold. Local
   `stdio` servers need no pacing.

   Part files land at `<shapes-path>.parts/<tool>.json` (git-ignored), with the tool name
   percent-encoded — a `/` in a tool name becomes `%2F`.
   Concurrent probes of *distinct* tools do not clobber each other's parts, the case-only
   collision above excepted — whether you may actually run them concurrently is the
   pacing question above, not a file-safety one.
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
   For discriminated tools (any tool in the polymorphic-suspect list from step 2.e),
   probe **each variant separately** and place its shape under the right variant key
   manually in step 4. A tool's part file is a single fixed path
   (`<shapes-path>.parts/<tool>.json`) and each probe **overwrites** it, so read that
   part and record the variant's shape *before* issuing the next variant's probe —
   otherwise only the last variant survives. **Threshold: 20 variants per discriminator.** Probe
   up to 20 without asking. Above 20, stop and put the choice to the user via
   `AskUserQuestion` — probe all N (each is a live call), probe a named subset, or fall
   back to a generic base model (step 4 option 2). Only an explicit user yes takes it
   past 20; each probe is a live call, so the threshold is a cost/blast-radius brake.

   > **Non-interactive fallback — when 2d's condition is not met and the count is
   > above 20**, since nothing can grant the exception: fall back to a generic base
   > model of common fields (step 4 option 2), or unwrap-only `Any` where no stable
   > shared base exists. Do **not** probe all N. At or under 20 the sweep proceeds
   > without asking, exactly as above — the threshold is what the interactive gate
   > guards, not the sweep itself.

   Enumerate discriminator values from: (a) the param's `enum` in `inputSchema`;
   (b) discovery tools / glossary / tool descriptions (e.g. `get_filters` /
   `get_entity_fields` per `entityType`, `get_acme_glossary`); (c) `AskUserQuestion`
   if not discoverable from available tools.

   **Check `inputSchema.required` before constructing probe args** (read from `list --schema`
   output). If the array is non-empty, never probe with `'{}'` — call the tool with minimal
   valid args on the first attempt. Invent realistic-looking but fake values for required
   string/ID args that carry no `enum`. For GitHub servers, prefer `owner: "microsoft",
   repo: "vscode"` as the default probe repo (`octocat/Hello-World` lacks releases/tags/issue
   fields and produces `[<empty>]` for those tools).

   **Inspect `inputSchema` for enum constraints before constructing probe args** (read from
   `list --schema` output, field `inputSchema.properties[param].enum`). If an `enum` array
   is present, use its **first listed value** as the probe arg instead of inventing a value —
   invented values will be rejected with an MCP validation error. Record the chosen value
   in `probed_args`. Note: codegen maps enum params to `Literal[...]` automatically, so the
   generated signature already encodes the allowed values.

   **JSON-in-string detection.** Some servers double-encode: the record arrives as a JSON
   *string* inside the MCP envelope (e.g. `directory_tree`), so the client parses the
   envelope and still hands you a `str`. When `_observed_shape == "str"`, test the raw
   payload already captured above — re-run `call --out` whenever the args differ from
   that tool's most recent capture, since the name is per tool, not per argument set,
   so a later capture overwrites the earlier one — with this guarded snippet, never a
   bare `json.loads()`, which exits
   non-zero on prose:

   ```python
   import json, pathlib
   try:
       raw = pathlib.Path("<server>.<tool>.probe-raw.json").read_text()
       parsed = json.loads(raw)
       label = "JSON_UNWRAP" if isinstance(parsed, (dict, list)) else "NOT_JSON"
       print(label, type(parsed).__name__)
   except (OSError, UnicodeError, json.JSONDecodeError) as exc:
       print("NOT_JSON", type(exc).__name__)
   ```

   Reading inside the `try` matters: a `call` that failed leaves no file, and an
   unreadable or non-UTF-8 one raises just as loudly as a bare `json.loads()` would.

   `JSON_UNWRAP dict` / `JSON_UNWRAP list` — re-enter shape analysis on the *parsed*
   object. `_dig` / `_dig_list` parse a JSON-encoded string at runtime, so the typed
   record does arrive — **but they are only emitted for a tool with a non-empty
   `unwrap`**, which splits the case in two:
   - The parsed object is itself an envelope (the record sits under `tree`, `results`,
     …) — set `unwrap` to that key path and derive `return_model` and `fields` from the
     record it reaches.
   - The parsed object *is* the record — there is no key path, so `unwrap` stays empty,
     nothing parses the string at runtime, and `return_model` must stay `null`. Never
     invent a path to force parsing on: `_dig` would return that field instead of the
     record, and a `TypedDict` would claim a dict the wrapper never returns.

   Record `"_json_unwrap": true` as a note for the next reader — codegen itself does not
   read that key. `NOT_JSON` is an **expected outcome**, not a probe failure: the payload
   is prose or a bare scalar, `_observed_shape: "str"` stands, and nothing is recorded as
   an error.

   **Image / binary / media tools.** `image`, `resource`, and `resource_link` blocks
   surface as small metadata dicts — `{"type", "mimeType", "has_data"}` and friends —
   never the base64 bytes, which are deliberately dropped to keep shape-specs small.
   The observed shape therefore describes the envelope, not the record. If a tool
   description mentions "image", "media", "audio", "base64", or "binary", leave the
   wrapper as `-> Any`, note it in `session-overview.md`, and do not model a payload
   the probe never saw.

   **Empty-store probes produce under-typed list fields.** If a read tool returns an
   empty list (`[]`), the inner element shape is unobservable. Do not fabricate a schema
   from zero samples — leave the field typed as `list`. Note in `session-overview.md`
   that the inner model is unobservable at probe time, and recommend re-running
   `mcpgen probe` after seeding the server with representative data (e.g. via
   `<mcpgen> call <server> <mutating-tool> --args '<json>' --out <server>.<mutating-tool>.probe-raw.json`)
   to capture inner field shapes.

   Sample args may need bootstrapping (e.g. a real id before probing `get_entity`).
   Find a no-arg / discovery tool on *this* server that returns user or entity ids (there
   is no universal tool for this — infer from `mcpgen list` output). Call it via
   `<mcpgen> call <server> <discovery-tool> --out <server>.<discovery-tool>.probe-raw.json` to capture the
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
   standard SQL queries, or anything not personally identifiable: the roundtrip verifier
   — the `run.py` smoke test step 7 generates, which replays `probed_args` live —
   passes `probed_args` to the live server, and the gitignored `<shapes-stem>.verify.json`
   sidecar holds the pre-scrub args for it.

   When a value *must* be redacted, add `"probe_args_scrubbed": true` to the shape-spec
   entry. The roundtrip verifier checks the sidecar first; `probe_args_scrubbed` is only
   needed when the sidecar is absent or does not cover that tool.

   The shape-spec must record *that* `entityType` was probed as `int` and the response
   *shape* — never the sample values. If you keep raw responses for reference, write them
   to `<server>.probe-raw.json` (git-ignored), not into the shape-spec.

3b. **Consolidate parts → shapes.json.**
   ```
   <mcpgen> merge <server> --out <shapes-path>
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
     tool flagged in step 2.e **that is in the selected set**, you MUST choose one of
     three options before the shape-spec is considered complete; a flagged tool outside
     the set stays recorded as unresolved. Default: probe all variants (≤20).
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
   - **Add** `_mutating_suspect` / `_mutating_reason` for every probed tool step 2b
     flagged. Here is the first point they can be added — merge would have replaced the
     entry — and they are never deleted afterwards.

5. **Regenerate.** Reach the server exactly the way step 1 did — one form, not both:
   ```bash
   # config form: the env prefix is part of the command, not an argument
   MCPGEN_SERVERS=servers.json <mcpgen> codegen <server> --out <server>/<server>.py --embed-schema
   # direct form: substitute step 1's own --stdio / --url (+ --bearer) values
   <mcpgen> codegen <server> --stdio "<launch command>" --out <server>/<server>.py --embed-schema
   # non-default shapes filename only: add --shapes <path>
   ```
   Never combine the two: `--stdio` outranks a config entry, so a command carrying both
   silently reaches a different server than step 1 probed.
   No `--shapes` is needed in the layout this skill uses. Auto-detection looks for
   `<server>.shapes.json` — built from the **server name**, not from the `--out`
   filename — in the `--out` directory, which is exactly where step 3b wrote it. For a
   URL-form server argument the stem is only the host, so the name will not match the
   paths you built from the URL: pass `--shapes` explicitly there. Pass
   `--shapes` only when the shapes file carries some other name. Two constraints when
   you do: it takes the file, never the `.parts/` directory, and passing it disables the
   in-memory parts fallback, so `codegen --shapes <path>` on a not-yet-merged run fails
   with `FileNotFoundError`.

   Now shaped tools return their `TypedDict` (or `list[<model>]`), unwrapping via
   `_dig` / `_dig_list`.

6. **Verify.** `ast.parse` the module; confirm the eval target — the shaped tool's
   signature reads `-> Entity` (not `Any`) and its body digs the envelope. Where a
   hand-built wrapper exists, diff the generated unwrap against it as an oracle.

7. **Generate a smoke-test runner.**

   Once the wrappers are shaped and verified, invoke `/mcp-client-kit:generate-mcp-runner`. Generation
   adds one new file, so it is the default in every non-interactive run — a subagent that
   cannot reach `AskUserQuestion` generates the runner rather than skipping the step.
   Runner generation needs `mcpgen >= 0.7.0` — a higher floor than step 0's. Step 0 kept
   the *first* invocation clearing 0.3.0, so a `0.6.x` on `PATH` can be the resolved one
   while a `0.7+` sits in the venv. Before skipping, re-run step 0's loop with `min=0.7.0`
   and use whatever it resolves. Skip *for engine age* only when nothing clears 0.7.0 —
   say so when you do; the two exceptions below are separate reasons to stop.
   When the output folder already holds a `run.py`, leave it alone and report that it
   exists; a hand-edited runner is not regenerated over.

   > **Interactive exception:** where 2d's condition holds — a session a user opened
   > directly — offer
   > the choice first (single-select, 2 options: **Yes** — invoke `mcp-client-kit:generate-mcp-runner`
   > now; **No / I'll do it later** — stop here) and honor the answer.

   Pass the following details so the runner skill does not need to re-derive them:

   - **The read-only tool set** — name the tools the runner may call, and the ones it must
     skip; this verdict overrides the runner's own classification. Pass it explicitly:
     the runner re-derives mutating tools from a fixed keyword list narrower than step
     2b's — it lacks `add`, `append`, `insert`, `upsert`, `push`, `close`, and `revoke`,
     and reads neither `annotations` nor the description semantically — so a tool only
     step 2 catches would otherwise be called for real.
   - **Resolved `mcpgen` invocation** — the literal string that cleared the 0.7.0 floor
     above (`mcpgen`, `uv run mcpgen`, or `.venv/bin/mcpgen`), which is not always the
     one step 0 settled on. Pass it explicitly: the runner skill gates
     on a bare `mcpgen`, which is absent in a uv-managed project, so without this it
     reports the engine missing on a machine where step 0 just used it.
   - **Server name** — `<server>` (the name used throughout this skill)
   - **Output folder** — the dir from `--out` (e.g. `<server>/`), which holds `<server>.py`,
     `<server>.shapes.json`, and `<server>.verify.json`
   - **Connection source** — exactly how step 1 reached the server:
     - config file: the `servers.json` path used via `MCPGEN_SERVERS=` / `--config`; **or**
     - direct params: `--stdio "<launch>"`, `--url "<url>"` (+ `--bearer "$ENV_VAR"` if applicable)
   - **Transport + auth kind** — the `(transport, auth_kind)` tuple, e.g. `(http, oauth)`,
     `(stdio, none)`, `(http, bearer)` — used by the runner to pick the right connection skeleton

   That skill reads the module, `shapes.json`, and `verify.json` you produced and authors a
   workflow-ordered, shape-aware smoke test in one step. See `skills/generate-mcp-runner/SKILL.md`
   for the full procedure. This step is a pointer only — do not duplicate the runner procedure here.

## Guards (do not violate)

- **Only mcpgen talks to the server.** Every live interaction — `list`, `probe`,
  `call`, bootstrap, inspect — goes through `mcpgen`. Never shell out to `curl`, `gh`,
  `httpie`, or raw `python` HTTP. mcpgen owns auth (browser OAuth + silent token
  refresh); any other client is unauthenticated, leaks the bearer token, or both.
  Need a raw payload? That's `<mcpgen> call … --out *.probe-raw.json` (git-ignored).

- **Never pipe any `mcpgen` command that talks to a server.** Stdout carries the
  artifact — `list` prints its JSON there, `codegen` the module whenever `--out` is
  absent — so a pipe over it truncates or discards the very thing you ran the command
  for. The status misleads on top of that: a pipeline reports the last command's status
  unless `pipefail` is set, so `… 2>&1 | tail` hands back `tail`'s exit code and a failed
  run reads as a success. Nor does `pipefail` make `2>&1` safe: the `mcp-remote` bridge
  logs its handshake to stderr and the `uvx`/`npx` launchers add install and resolution
  noise, all of it folded into what you read back. Keep the streams apart — send
  **stderr alone** to a file: `<mcpgen> codegen … 2>codegen.log`, then read
  `codegen.log` separately.

- **Run one `<mcpgen> call` per shell invocation.** Give two chained calls the same
  `--out` — the per-tool `<server>.<tool>.probe-raw.json` naming exists to stop exactly
  that — and the second overwrites the first, leaving only whichever
  call last succeeded, with nothing in the file to say which that was. Give them distinct
  names and the payloads survive, but a `;`- or newline-separated chain reports only its
  *last* command's status, so an earlier failure reads as success. A failing `call` writes nothing at all (every
  error path returns before the write), which leaves that call's file either missing or
  holding an earlier run's payload — and a chain that exited 0 gives you no reason to
  look. If you must chain, give each call **both** its own `--out` and a per-call status
  label, as step 3's accumulator does. (Independent `probe` invocations still batch — see
  step 3; probes of *distinct* tools write their own part files, the case-only collision
  above excepted.)

- **Bound a slow `mcpgen` call with the harness's own timeout, never a `timeout`
  binary.** Nothing guarantees one is installed: macOS ships none by default, and there
  the wrapped command never runs at all — the whole line dies with `command not found`
  and status 127, which reads as the command having failed rather than never having
  started.

- **Probing is a live call — mutating tools mutate.** The default selects every
  *non-mutating* tool — pruned or not, per step 2c — and probing a mutating tool (`create`/`update`/`delete`/`send`/etc., or
  `annotations.readOnlyHint: false`) executes it for real against the server. Flag
  likely-mutating tools in the step 2 report — `readOnlyHint` first, keyword+semantic
  heuristic as fallback, per step 2b — and keep them out of the selected set unless the
  user explicitly opts one in. Never probe a destructive tool "to see its
  shape" without explicit user confirmation.

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
  single-variant probe.** If a tool takes a discriminator arg (flagged in step 2.e),
  every sibling tool sharing that arg is polymorphic-suspect until probed across
  values or resolved to a base model / `Any`. A single-variant model is a silent lie
  for all other variants: typing a tool `list[Person]` from one `entityType` value
  misdescribes every response the other values return.
- **Scalar enum params render as `Literal[...]` automatically** — do not hand-widen them
  to `str`. Codegen's `py_type()` emits `Literal['a', 'b', ...]` for a param whose
  `inputSchema` carries an `enum` array of PEP 586-safe scalars (no flag required);
  float and object members fall back to `float` / `dict`. Widen to `str` only if
  the server actually accepts values outside the declared enum.
