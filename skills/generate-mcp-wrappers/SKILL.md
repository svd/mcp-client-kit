---
name: generate-mcp-wrappers
description: Use when generating typed Python wrappers for an MCP server. Drives `mcpgen` to emit mechanical stubs, then applies live-probe findings (vendor-envelope unwrap, schema-lied types, nullability) via a shape-spec sidecar so the generated wrappers return typed records instead of opaque `Any`.
---

# Generate typed MCP wrappers (the judgment pass)

`mcpgen codegen` does the deterministic 80%: `tools/list` → one typed `async def` per tool
against the `McpCaller` seam, and one `read_*` accessor per resource and resource template
against the `McpResourceReader` seam. Both return `Any` and are blind to vendor response
envelopes.
**This skill is the 20% judgment that an LLM must do:** probe a real call, read the actual
response, and record what the input schema couldn't tell you — into a **shape-spec sidecar**
(`<server>.shapes.json`, data not code) that codegen re-consumes to emit unwrap helpers and
`TypedDict` return models.

## Reference files

Load these when the step that names them says to. They hold detail that most runs never need,
so reading them up front is wasted context.

| File | Read it when |
|---|---|
| `references/probe-failures.md` | a probe returns `"str"`, an error, a traceback, or hangs |
| `references/mutating-tools.md` | classifying tools as safe-to-probe in step 2b |
| `references/shape-spec.md` | filling in shape-spec fields in step 4 |
| `references/subagent-execution.md` | you are the main thread with >4 selected tools |

## Shell rules

These bite in every step, so they are stated once here rather than repeated per command.

- **Quote every substituted value.** A server name is a config key or a URL, so it can hold
  spaces, `?`, `&`, or glob characters — `"my server"`, `"my server/my server.py"`. Unquoted,
  it word-splits or globs in `sh`/`bash`, and globs in `zsh`.
- **`<mcpgen>` is the absolute path step 0 prints, pasted in literally.** Shell variables and
  functions do not survive between invocations, so `$MCPGEN` is gone by step 1. Do not wrap
  the command in a variable or function of your own either: a `c="uv run mcpgen"` run as a
  bare `$c` does not word-split in `zsh`, so every line of the batch runs the wrong command.
  State the path once in your report, then substitute it wherever a block below writes
  `<mcpgen>`.
- **An env prefix must be in the same invocation as the command.** `MCPGEN_SERVERS=servers.json
  <mcpgen> …` — it does not persist across calls.
- **Never `|| echo` a failure.** It returns 0 and hides the error, including from `set -e`.
  Accumulate instead: `… || { echo "FAILED <label>"; bad=1; }` per line, then
  `[ "$bad" -eq 0 ] || exit 1`.
- **A `;`- or newline-separated chain reports only its last command's status.** An earlier
  failure reads as success. Use `set -e` to fail fast, or the accumulator above to collect
  every result in one pass.
- **Never pipe an `mcpgen` command that talks to a server** — see Guards.

## Execution model

A single driver thread works for servers with ≤ ~4 selected tools, and is **required** when
this skill itself is running as a subagent — do not dispatch sub-subagents.

Above ~4 selected tools, and only as the main thread of a session, fan out: read
`references/subagent-execution.md` for the phase table, the batching rule (sibling sets stay
in one batch), the recon agent, and the rich agent contract. The dividing constraint is that
**subagents cannot call `AskUserQuestion`**, so every interactive gate and every deterministic
barrier stays on main.

## Procedure

0. **Resolve the CLI.** Requires `mcpgen >= 0.11.0.dev2`. Do not proceed on an older one.

   The command is often not on `PATH`: in a `uv`-managed project it lives inside the venv.
   Probe the three forms and keep the first that both answers **and** meets the floor — an
   outdated `mcpgen` on `PATH` must not shadow a current one in the venv. Each form is first
   resolved to the absolute path of the executable it would run, and the floor check runs on
   that path, so what the loop settles on is always one word:

   ```bash
   min=0.11.0.dev2; MCPGEN=
   vkey() {  # PEP 440 version -> dotted key that sort -V orders correctly
     local r s
     r=${1%%[!0-9.]*}; r=${r%.}; s=${1#"$r"}; s=${s#.}
     case $s in
       '')   s=4.0 ;;
       dev*) s=0.${s#dev} ;;
       a*)   s=1.${s#a} ;;
       b*)   s=2.${s#b} ;;
       rc*)  s=3.${s#rc} ;;
       post*) s=5.${s#post} ;;
       *)    return 1 ;;
     esac
     printf '%s.%s\n' "$r" "$s" | grep -E '^[0-9]+(\.[0-9]+)+$'
   }
   mkey=$(vkey "$min")
   for c in "mcpgen" "uv run mcpgen" ".venv/bin/mcpgen"; do
     case $c in
       "uv run mcpgen")
         p=$(uv run python -c 'import shutil; print(shutil.which("mcpgen") or "")' 2>/dev/null) ;;
       */*) p=$PWD/$c ;;
       *)   p=$(command -v "$c") ;;
     esac
     case $p in /*) [ -x "$p" ] || continue ;; *) continue ;; esac
     out=$("$p" --version 2>/dev/null) || continue
     ver=$(printf '%s\n' "$out" | awk '{print $2}')
     key=$(vkey "$ver") || { echo "skipping $p (unparsable version '$ver')"; continue; }
     [ "$(printf '%s\n%s\n' "$mkey" "$key" | sort -V | head -n 1)" = "$mkey" ] || \
       { echo "skipping $p ($ver < $min)"; continue; }
     MCPGEN=$p; break
   done
   [ -n "$MCPGEN" ] || { echo "no mcpgen >= $min found on any invocation"; exit 1; }
   echo "resolved: $MCPGEN ($ver)"
   ```

   `uv run mcpgen` becomes the path `uv run` would execute, found by `shutil.which` inside the
   project environment; `.venv/bin/mcpgen` is anchored to `$PWD`; a bare name goes through
   `command -v`, which prints nothing usable for an alias or a shell function, so those are
   skipped. Anything that is not an absolute path to an executable file is skipped the same
   way.

   `sort -V` alone misorders PEP 440: it puts `0.11.0.dev1` above `0.11.0`. `vkey` fixes that
   by splitting the release part from the suffix and appending a rank for the suffix, so
   `.devN` < `aN` < `bN` < `rcN` < final < `.postN`: `0.11.0.dev1` becomes `0.11.0.0.1` and
   `0.11.0` becomes `0.11.0.4.0`. The `$`-anchored `grep` rejects anything the ranks do not
   cover, such as a combined `rc1.dev2`, rather than letting it through as its own prefix.

1. **Mechanical stubs.**

   `mcpgen` resolves a server **by name** from a config file, pointed to by the
   `MCPGEN_SERVERS` env var or the `--config` flag. This is the primary form: the bare
   `<server>` name maps to its transport and forwards the server's `env` block (API keys, etc.)
   to the launched process.

   `codegen` does **not** create parent directories, so whenever `--out` names a folder,
   create it first or the run dies with `FileNotFoundError`.

   ```bash
   mkdir -p "<server>"
   MCPGEN_SERVERS=servers.json <mcpgen> codegen <server> --out <server>/<server>.py --embed-schema
   ```

   **Alternative — pass transport values directly, without a config file.** Swap the transport
   flag per variant; the `--out … --embed-schema` tail is identical in each:

   ```bash
   <mcpgen> codegen <server> --stdio "uvx mcp-server-time" --out <server>/<server>.py --embed-schema
   <mcpgen> codegen <server> --url "https://mcp.example.com/mcp" --out <server>/<server>.py --embed-schema
   <mcpgen> codegen <server> --url "https://api.example.com/mcp/" --bearer "$MY_TOKEN" --out <server>/<server>.py --embed-schema
   ```

   Only the config form forwards a server's `env` block. With direct `--stdio` you must repeat
   `--env` (once per variable) for every credential the server process needs, or it starts
   unauthenticated. `--env KEY` forwards `$KEY` from the current shell; `--env KEY=VAL` sets it
   inline. It is a no-op for non-stdio *transports* only: against a config-declared stdio
   server it merges over that server's own `env` block, so one extra variable never costs you
   the config's block.

   `codegen`, `list`, `probe`, `call`, and `read` each require the same transport flags
   (`--stdio` / `--url` / `--bearer` / `--env` / `--config`) on **every** invocation — they do
   not inherit flags from a prior run. `merge` and `discover` need no connection flags (`merge`
   accepts `--config` and ignores it). Later command blocks elide those flags for brevity: add
   the same transport form this step used, including the `MCPGEN_SERVERS=` prefix, to every
   runnable `codegen`, `list`, `probe`, `call`, and `read` line below.

   The result parses, with every tool typed from `inputSchema`, and returns `Any` until a
   shape-spec exists beside the output (step 5 regenerates once it does). `--embed-schema` also
   emits `fn.__schema__ = {<raw inputSchema>}` on each function and an Args docstring section
   listing each param's description, enum values, and default.

   When the server exposes resources, the module also holds one accessor per static resource
   and resource template, named from the URI: `risk://dictionary` → `read_risk_dictionary`,
   `dimension://by-unit-type/{unitType}` → `read_dimension_by_unit_type(caller, *, unitType:
   str)`. `codegen` prints the resource counts on stderr. Above 50 static resources it warns
   that the server may list its data as resources; step 2b decides whether to narrow with
   `--resources templates` or `--resources none`. A failed resource listing exits 1 with a
   hint to retry with `--resources none`, which generates the tool wrappers alone.

2. **Select tools and resources to probe.**

   Keep a running `session-overview.md` beside the generated module (the `--out` dir). It is
   the human-readable log for everything the shape-spec cannot hold: skipped mutating and
   interactive tools, unprobed tools and why, failed commands, and discriminator verdicts.

   List each tool whose record shape no probe observed under a `## unprobed` heading, one line
   apiece — tool, cause, detail — with the first cause below that fits:

   - `id-gated` — a required argument takes an id no read-only tool supplied. Name the argument
     and, when known, the producer tool that would mint it. Never run a mutating producer to
     get one: it stays skipped without step 2c's explicit human yes.
   - `entitlement-gated` — the server says the plan or account lacks the tool. Quote its
     machine-readable class (`tool_error_class: entitlement_required`), else its message.
   - `empty store` — the listing tool returned no items, so no record shape was observed.
   - `tool error` — `probe` printed `[probe] tool returned isError: …`; cite that line.
   - `timeout` — `probe` printed `[probe] timeout: …`; cite that line.
   - `interactive`, `mutating` — skipped by policy and logged under `## mutating-skipped`
     (`references/mutating-tools.md`), not repeated here.
   - `inconclusive` — the residue: the probe ran but told nothing usable. Say what came back,
     plus any note `references/probe-failures.md` asks for.

   A probe batch or command that failed stays in the log with the error it printed, even when
   a re-issue succeeded: say what failed, why, and what the retry changed. Write "no errors"
   only for a session in which nothing failed — a clean retry is the fix, not evidence that
   the first run was clean.

   a. Run `<mcpgen> list <server> --schema` → `[{name, description, inputSchema}]` for every
      tool, plus `annotations` on the tools whose server supplies it (the key is absent when it
      does not). **Probe only tools that appear in this output.** Do not add tools from
      system-prompt context, documentation, or prior knowledge. This output's `inputSchema` is
      the authoritative source for step 3's required-arg and enum checks — no separate schema
      fetch is needed.

      The same run prints a stderr line when the server has resources —
      `[list] 2 resource(s) and 1 resource template(s) available; …`. Its counts cover the
      first page only (`N+` means more pages). When that line appears, or
      `[list] note: could not list resources` does (an error or a 10 s timeout), run
      `<mcpgen> list <server> --resources` → `{"resources": [{uri, name, title, description,
      mimeType, size}], "resourceTemplates": [{uriTemplate, name, title, description,
      mimeType}]}`. It pages through both lists and fails loudly where the preview only noted
      the error. With neither line, the server has no resources: record `resources: none` and
      skip every resource step below.

   b. **Classify and report.** Decide safe-to-probe per `references/mutating-tools.md` —
      `annotations.readOnlyHint` first, keyword test plus semantic read as fallback, and record
      every verdict that isn't a clean `readOnlyHint`. A tool that waits for a human (input,
      confirmation, a pick in the browser) is interactive and skipped like a mutating one. Then
      print:

      ```
      Tools on <server>:
        get_entity      — Fetch a single entity by id and type
        query_acme      — Search entities matching criteria
        whoami          — Return the calling user's profile
        ⚠ create_entity — Create a new entity [MUTATING]
        ⚠ pick_entity   — Wait for the user to pick an entity in the browser [INTERACTIVE]
        ...
      ```

      Note for focus: the goal is to shape-spec tools that carry real records and whose
      payloads you want out of model context ("big dump" tools). Mutations and acks rarely need
      a `TypedDict`, so the default set below may be pruned to the record-carrying tools — say
      in `session-overview.md` what you dropped and why. **Prune before sizing the run:** the
      Execution model picks single-driver vs fan-out from how many tools are *selected*.

      **Resources are read-only.** Reading one never changes the server, so resources need no
      mutating classification and no opt-in; the 2c/2d gates below govern tools alone. Select
      resources by what they hold:
      - **Every template** (`dimension://by-unit-type/{unitType}`) — a parameterized lookup, the
        resource form of a read tool.
      - **API-like statics** — reference data the tools depend on: a dictionary, a catalog, a
        schema, a config. A tool description that says "read `risk://dictionary` before
        calling" names one outright.
      - **Skip data-like lists** — statics that enumerate records one URI each
        (`file:///…/a.txt`, `file:///…/b.txt`, `db://orders/1041`, …). List them in
        `session-overview.md` as skipped data resources. Skipping means not probing: `codegen`
        still emits an accessor for every listed static. Where the statics are data-like as a
        group (codegen warns above 50), regenerate in step 5 with `--resources templates`
        (templates only) or `--resources none`, and record the choice in the overview.

      Print the resource verdicts under the tool list:

      ```
      Resources on <server>:
        risk://dictionary                    — Risk categories and levels [static]
        dimension://by-unit-type/{unitType}  — Dimensions for one unit type [template]
        ✗ file:///data/orders/1041.json      — [data-like, skipped]
      ```

   c. **Select the set — default path.** The default is **every non-mutating tool**, optionally
      pruned per the focus note. Mutating tools are skipped entirely — never probed without an
      explicit human yes. This is the else-branch of 2d: take it whenever 2d's condition is not
      met.

      Decide the prune on the transport, which you know before sizing anything: against a
      hosted HTTP server every probe is serial and paced, so prune to the record-carrying
      tools. Against a local `stdio` server keep the full set — probes there are cheap and may
      fan out.

   d. **Interactive exception.** Take this path only when you are the main thread of a session
      a user opened directly — not a subagent prompt, not a `-p` batch run. `AskUserQuestion`
      being *available* is not the test: it sits in the roster on a headless run too, where
      nothing answers it. If you ask and the question comes back unanswered, fall back to 2c
      and say so in `session-overview.md`.

      Ask first (single-select, 3 options); every option draws from the step-2b-cleared set, and
      any mutating tool wanted in any of them takes a separate explicit yes:
      - **Probe all non-mutating** *(recommended)* — every tool step 2b cleared.
      - **Confirm in batches** — 4-at-a-time multi-select questions; `label` = tool name,
        `description` = tool description. After 16 options, ask whether to continue. The union
        of checked options is the selected set.
      - **I'll specify the tools** — user names them (free-text via "Other"); confirm any
        ambiguous name before probing, and any named tool step 2b flagged.

   The selected set (from either path), plus the resources 2b selected, drives steps 3 and 4.

   e. **Detect discriminators.** The advisory is stderr of the *same* `list --schema` run step
      2.a already made, so the answer is in hand before any analysis.

      *Precondition — use it to tell a genuinely absent advisory from an unread one.* A
      candidate exists only where two or more tools declare a parameter under the **same name,
      same case**, carrying a top-level `"type"` of exactly `"integer"`, `"number"`, or
      `"string"`, and absent from the engine's own denylist (the lowercased-exact-name list in
      Pass 1's first sentence, **not** the camelCase additions Pass 1 layers on top). A union
      such as `["string", "null"]`, or a scalar expressed only through `anyOf`/`oneOf`, does not
      clear the type test.

      Where nothing clears all four, no advisory can fire — run the description sweep below
      before recording `discriminators: N/A`. Two tools sharing only `page`, only a boolean or
      object param, or `entityType` against `entitytype` all fail it; `maxResults` and `filePath`
      **pass** it, because the engine does not drop those. Otherwise the advisory names what
      survived:

      ```
      [list] ⚠  discriminator candidates (response shape varies by these args):
      [list]   entityType → export_excel, get_entity, get_entity_fields, …
      [list]   Probe EACH value or use a base model — do NOT type from one probe. See SKILL step 4.
      ```

      **The advisory is not the only source — sweep the descriptions too.** Its precondition
      needs a shared scalar, so two real discriminators are invisible to it by construction: one
      confined to a **single** tool, and one whose type is an array. Read the descriptions of the
      selected tools for a param that names what comes back — `Response key: products |
      service_apis | cfn_resources` is a discriminator declaring itself in prose. Treat a hit as
      a candidate like any other: carry it into Pass 2, resolve it in step 4. Enumerate its
      values from the description, since a param like this often carries no `enum` to read them
      from. Record `discriminators: N/A` only when this sweep comes up empty too.

      Record every candidate and the tools it spans. A discriminator found on one tool **drives
      its siblings** — every tool in that list is *polymorphic-suspect*, and stays so through
      Pass 2, which can confirm a candidate but never disprove one. An **inconclusive** Pass 2
      does not clear it: the tools remain polymorphic-suspect and step 4 must still resolve
      them. That is what "flagged in step 2.e" means wherever the rest of this file says it.

      Resolution is mandatory for siblings **inside the selected set**; a sibling outside it is
      recorded as unresolved, never probed to close the gap.

      **Pass 1 — auto-disqualify by name.** The advisory is already pre-filtered: `list` drops
      `page`/`per_page`/`limit`/`offset`/`cursor`/`path`/`repo`/`owner`/`org`/`branch`/`ref`/
      `method`/`query`/`search`/`filter`/`sort`/`order`/`direction`/`context_lines`/`include`/
      `exclude` and exactly five identity forms — `reponame`, `repo_name`, `repositoryname`,
      `username`, `orgname` — matching lowercased exact names. That list is the whole of it: any
      other `*name` param (`fileName`, `entityName`) reaches you. Do not re-check what it drops.

      Pass 1 catches the camelCase and alternate spellings that exact match misses. No reasoning
      required; these steer a query, never the shape:
      - **Pagination / window:** `perPage`, `head`, `tail`, `since`, `after`, `before`,
        `maxResults`, `count`
      - **Sort / order:** `orderBy`, `order_by`
      - **Path identity:** `filePath`, `file_path`, `repoPath`, `repo_path`, `projectPath`,
        `workspacePath`

      Breadth is **not** a disqualifier. A parameter every selected tool accepts can still be a
      real discriminator — on some servers every tool takes `entityType`. Judge a candidate by
      what it does to the response, never by how many tools share it.

      **Pass 2 — confirm by comparing response shapes.** This pass makes live calls, so it runs
      at the **start of step 3**, after the ignore preflight and once `<shapes-path>` exists —
      not here. Record the surviving candidates now; confirm them there.

      For each candidate that survived Pass 1, pick one **non-mutating** tool that takes it and
      probe **three distinct values** of that parameter — or every value, if its `enum` has
      three or fewer — holding every other argument fixed.

      *Normalize before comparing.* `_observed_shape` renders a list of more than one element as
      `[<element shape>, "...xN"]`, where `N` is that response's element count. `N` tracks how
      much data came back, not the shape, so two identical shapes differ textually whenever the
      counts differ. Ignore every `...xN` sentinel; a differing `N` alone is **not** a shape
      difference.

      Then judge:
      - **Any two values differ** — a key present in one and absent in the other, or the same
        key with a different type → confirmed discriminator. Resolve it per step 4. Stop probing
        this candidate; one difference is proof.
      - **All probed values identical** → **inconclusive, not disproven.** Three samples of one
        tool cannot clear the parameter for its siblings: a fourth value, or the same value on a
        different sibling, may still switch shape. Stop probing the candidate here, but it stays
        polymorphic-suspect: resolve it in step 4 like any other flagged tool — option 1 where
        the variants can be enumerated and probed, otherwise option 2 or 3 — and record it in
        `session-overview.md` as unconfirmed, with the tool and the exact values compared.
        Never let an inconclusive result promote a single-variant model to a confident one.

      Do **not** require the parameter to appear as a key in the response. A server can switch
      shape on an argument it never echoes back — `entityType=1` may return plain `Person` fields
      with no `entityType` key — so absence from the payload proves nothing either way.

      This costs up to three live calls per surviving candidate. That is the price of the answer:
      guessing from names alone is what produces a confident `list[Person]` for a tool that
      returns something else at `entityType=7`. Issue the values as **separate `probe`
      invocations**, never as repeated `--args` in one — a single probe deep-merges all its calls
      into one `_observed_shape`, unioning away the very difference this pass looks for. Each
      probe overwrites the tool's part file: read the shape between calls, or only the last one
      survives.

      **Resources carry no discriminators.** A template's variables choose *which* resource is
      read, not a response variant, so neither pass applies to resources and `probe --resource`
      prints no advisory.

3. **Probe each selected tool and resource → skeleton (parallel-safe).**

   First, establish `<shapes-path>` — the consolidated shapes sidecar. It must sit **beside the
   generated module** so `mcpgen codegen` auto-detects it:
   - CWD output (default): `<shapes-path>` = `<server>.shapes.json`
   - Subfolder output (e.g. `github/github.py`): `<shapes-path>` = `github/github.shapes.json`

   Use the **same `<shapes-path>` value** in every probe (step 3), the merge (step 3b), and in
   codegen `--shapes` on the rare run that passes it.

   **Ignore-rule preflight — run before the first live call of any kind**, including the recon
   subagent's. Probing writes raw ids and PII into three artifacts, and `mcpgen` installs no
   ignore rules.

   Two of the three paths follow `<shapes-path>`, not the CWD: `merge` writes the verify sidecar
   beside the shapes file. Write `<shapes-stem>` for `<shapes-path>` with the `.shapes.json`
   suffix removed (`github/github.shapes.json` → `github/github`). Only `probe-raw.json` is
   CWD-relative. Raw payloads are named **per tool** — `<server>.<tool>.probe-raw.json` — so an
   ignore rule covering them must be a `*.probe-raw.json` glob, not one literal path.

   ```bash
   bad=0
   for p in "<shapes-path>.parts/" "<shapes-stem>.verify.json" "<server>.<tool>.probe-raw.json"; do
     git check-ignore -q "$p" || { echo "NOT IGNORED — add to .gitignore: $p"; bad=1; }
   done
   [ "$bad" -eq 0 ] || { echo "ignore preflight FAILED — do not probe"; exit 1; }
   echo "ignore preflight OK"
   ```

   Add any pattern reported here and re-run until it prints `ignore preflight OK`. Do not
   proceed with an unignored path.

   > **Fan-out (>4 selected tools, main thread only):** with the preflight green and
   > `<shapes-path>` fixed, dispatch the recon subagent, then batch the probe agents. See
   > `references/subagent-execution.md`. Against a hosted HTTP server, do **not** fan out —
   > parallel agents cannot hold the ≥ 2 s interval below.

   **Then run step 2.e's Pass 2** — the variant probes per surviving candidate — before the main
   probe sweep. Skip it outright when step 2.e recorded `discriminators: N/A`. It runs *after*
   recon because a candidate whose values are not declared in its `enum` can only get them from
   the recon catalog. Pass 2's verdicts, confirmed **or inconclusive**, decide which tools stay
   polymorphic-suspect, and that decides how many variants the sweep has to cover.

   ```
   # single probe
   <mcpgen> probe <server> <tool> --args '<sample json>' --emit-shape <shapes-path>

   # multi-probe: repeat --args for each input; shapes are deep-merged within one probe
   <mcpgen> probe <server> <tool> \
     --args '{"entityId":"<id1>","entityType":1}' \
     --args '{"entityId":"<id2>","entityType":1}' \
     --emit-shape <shapes-path>
   ```

   **Probes for distinct tools are independent — issue them in one batch.** Each tool writes its
   own part file, so nothing serializes them. Put every probe in a single shell invocation (one
   `<mcpgen> probe` per line); do not spend one turn per tool. Against a local `stdio` server
   they may also go out as parallel tool calls; against a hosted server keep them sequential and
   interleave `sleep 2`. The step-3b read afterwards is **one file read** of the merged
   `<shapes-path>` — not one read per tool.

   ```bash
   set -e   # fail-fast: without it the batch reports only the last probe's status
   <mcpgen> probe <server> tool_a --args '<args-for-tool_a>' --emit-shape <shapes-path>
   sleep 2  # hosted servers only; drop it for local stdio
   <mcpgen> probe <server> tool_b --args '<args-for-tool_b>' --emit-shape <shapes-path>
   ```

   `set -e` stops the batch at the first failure — the tools after it go unprobed and must be
   re-issued. Drop it and check each status instead (per the Shell rules accumulator) when you
   would rather collect every result in one pass. Either way, label each check with the tool name
   yourself: `probe` announces the tool before calling it, but an unparseable `--args` payload
   fails ahead of that line and names nothing.

   **Resources — `probe --resource`.** The target is the URI or template exactly as
   `list --resources` printed it; quote it, since `{`, `?`, and `&` are shell metacharacters.
   The probe reads through `read_resource`, the path the generated accessor takes, so the part
   records the shape the accessor will return.

   ```bash
   set -e
   # static resource: keyed by its normalized URI; takes no --args
   <mcpgen> probe <server> 'risk://dictionary' --resource --emit-shape <shapes-path>
   # template: keyed by the template string; each --args is one read's variables
   <mcpgen> probe <server> 'dimension://by-unit-type/{unitType}' --resource \
     --args '{"unitType":"PROJECT"}' --emit-shape <shapes-path>
   ```

   - **Real variable values.** Key `--args` by the template's raw variable names. A variable
     names an existing object as often as a tool's id argument does, so take its value from
     the same places: the recon catalog, a discovery tool, or a static resource that
     enumerates the values (`dimension://all` lists the unit types). An invented value reads
     back an error or an empty result, not the record.
   - **Argument errors.** A template with a required variable missing from `--args`, or
     `--args` on a static URI, exits 1 before any live read.
   - **Keys.** The entry for a static URI is keyed by its normalized form (`Dimension://ALL`
     → `dimension://ALL`); a template's entry by the template string as given. `codegen`
     matches entries against the listed URIs and templates, so a template typed with a
     different variable name (`{id}` for `{record_id}`) applies to nothing — step 5 warns.
   - **Blobs.** A binary resource's shape is `"bytes"`; `--save-raw` writes its payload
     base64-encoded. Name raw files `<server>.<accessor>.probe-raw.json`
     (`<server>.read_risk_dictionary.probe-raw.json`) so the preflight's glob covers them.
   - **Inspecting without a shape.** `<mcpgen> read <server> '<uri>' [--args '<json>'] --out
     <server>.<accessor>.probe-raw.json` is the resource twin of `call --out`: one text
     content is written verbatim, one blob as raw bytes, several contents as a JSON array.

   **Pacing for hosted endpoints.** Against an HTTP/remote server, leave **≥ 2 s between live
   probes**. The multi-`--args` form fires its calls back-to-back inside a single invocation, so
   against a hosted server split a tool's variant probes into separate, paced invocations,
   reading the part file between them. Local `stdio` servers need no pacing.

   **When a probe fails, hangs, or returns `"str"`** — read `references/probe-failures.md`
   before recording anything. It separates quota/auth (never retry), challenge/transport
   (bounded backoff), settled facts, and `--timeout` expiries, and it covers the `uvx`/`npx`
   cold-start tracebacks that are noise on a successful probe.

   **Part files.** They land at `<shapes-path>.parts/<key>.json` (git-ignored), where `<key>`
   is the URL-quoted tool name, URI, or template; a key too long for a filename is cut short
   and ends in a hash. Concurrent probes of *distinct* keys do not clobber each other, with one
   exception: filenames preserve case, so on a case-insensitive filesystem (macOS's default)
   two keys differing only in case (`Search` and `search`, `demo://x/Users` and
   `demo://x/users`) share one part file. `probe` refuses the second with an error naming the
   first rather than overwriting it. Run step 3b to merge the first, then probe the other and
   merge again.

   **Deep merge.** Each `--args` makes one live call. Observed shapes are deep-merged: keys are
   unioned (a key absent from some probes is kept — `total=False` covers it), type conflicts
   widen (`str`+`NoneType` → `str | None`; `int`+`float` → `float`; other concrete conflicts →
   `Any`, or `Any | None` when a null was seen too). `_observed_shape` reflects the merged
   result, and `fields` pulls out the merged top-level scalars. With multiple probes,
   `probed_args` is a list of arg-dicts; with a single probe it stays a plain dict.

   Use multi-probe when: (a) some fields are nullable/optional, (b) the same tool is called with
   different ids and you want all visible field variants, (c) a discriminated tool has multiple
   response shapes per variant that you want to union.

   **Discriminated tools — probe each variant separately.** For any tool in the
   polymorphic-suspect list from step 2.e, probe each variant on its own and place its shape
   under the right variant key manually in step 4. A tool's part file is a single fixed path and
   each probe **overwrites** it, so read that part and record the variant's shape *before*
   issuing the next variant's probe — otherwise only the last variant survives.

   **Threshold: 20 variants per discriminator.** Probe up to 20 without asking. Above 20, stop
   and put the choice to the user via `AskUserQuestion` — probe all N (each is a live call),
   probe a named subset, or fall back to a generic base model (step 4 option 2). Only an explicit
   user yes takes it past 20.

   > **Non-interactive fallback — when 2d's condition is not met and the count is above 20**,
   > since nothing can grant the exception: fall back to a generic base model (step 4 option 2),
   > or unwrap-only `Any` where no stable shared base exists. Do **not** probe all N. At or under
   > 20 the sweep proceeds without asking — the threshold is what the interactive gate guards,
   > not the sweep itself.

   Enumerate discriminator values from: (a) the param's `enum`, `const`, or `anyOf`/`oneOf` of
   `const`/`enum` branches in `inputSchema`; (b) discovery tools / glossary / tool descriptions
   (e.g. `get_filters` / `get_entity_fields` per `entityType`); (c) `AskUserQuestion` if not
   discoverable from available tools — and where 2d's condition is not met there is no (c):
   probe the values you did discover, and record the candidate as unconfirmed with the values
   you could not enumerate.

   **Check `inputSchema.required` before constructing probe args.** If the array is non-empty,
   never probe with `'{}'` — call the tool with minimal valid args on the first attempt. For a
   required arg that **references an existing object** (an id, key, or slug), use a real value
   from the recon catalog or a discovery tool: a server that validates existence answers an
   invented id with an error, and you record an error shape instead of the record — or, if the
   tool flags `isError`, the probe fails. Invent values only for required string args that
   reference nothing (a free-text query, a label, a title). For GitHub servers, prefer
   `owner: "microsoft", repo: "vscode"` — real, public, and carrying releases, tags, and issues
   to observe.

   **Inspect `inputSchema` for enum constraints.** A param pins its values with an `enum` array,
   a `const`, or an `anyOf`/`oneOf` whose branches each carry a `const` or `enum` (Zod and
   pydantic emit this, often with a `{"type": "null"}` branch). If any of these is present, use
   the **first listed non-null value** rather than inventing one — a validating server rejects
   invented values, and mcpgen itself does not check. Record the chosen value in `probed_args`.
   Codegen maps all three forms to `Literal[...]` automatically when the values are scalars (a
   float falls back to the declared type in an `enum`/`const` and to `Any` in a union), so the
   generated signature already encodes the allowed values.

   **JSON-in-string detection.** Some servers double-encode: the record arrives as a JSON *string*
   inside the MCP envelope (e.g. `directory_tree`), so the client parses the envelope and still
   hands you a `str`. When `_observed_shape == "str"`, test the raw payload captured per
   `references/probe-failures.md` — re-run `call --out` whenever the args differ from that tool's
   most recent capture, since the name is per tool, not per argument set. Use this guarded
   snippet, never a bare `json.loads()`, which exits non-zero on prose:

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

   Reading inside the `try` matters: a `call` that failed leaves no file, and an unreadable or
   non-UTF-8 one raises just as loudly as a bare `json.loads()` would.

   `JSON_UNWRAP dict` / `JSON_UNWRAP list` — re-enter shape analysis on the *parsed* object.
   `_dig` / `_dig_list` parse a JSON-encoded string at runtime, so the typed record does arrive —
   **but they are only emitted for a tool with a non-empty `unwrap`**, which splits the case in
   two:
   - The parsed object is itself an envelope (the record sits under `tree`, `results`, …) — set
     `unwrap` to that key path and derive `return_model` and `fields` from the record it reaches.
   - The parsed object *is* the record — there is no key path, so `unwrap` stays empty, nothing
     parses the string at runtime, and `return_model` must stay `null`. Never invent a path to
     force parsing on: `_dig` would return that field instead of the record, and a `TypedDict`
     would claim a dict the wrapper never returns.

   Record `"_json_unwrap": true` as a note for the next reader. `NOT_JSON` is an **expected
   outcome**, not a probe failure: the payload is prose or a bare scalar, `_observed_shape: "str"`
   stands, and nothing is recorded as an error. That includes a fixed notice a tool returns
   whatever the args — a deprecation, "moved to …" — often flagged `[DEPRECATED]` in its title
   or description: the tool answered, deterministically, so it is observed, not unprobed and not
   `inconclusive`. Keep the wrapper `-> Any` and say in `session-overview.md` that it is
   deprecated, quoting the notice.

   **Image / binary / media tools.** `image`, `resource`, and `resource_link` blocks surface as
   small metadata dicts — `{"type", "mimeType", "has_data"}` and friends — never the base64
   bytes, which are deliberately dropped to keep shape-specs small. The observed shape therefore
   describes the envelope, not the record. If a tool description mentions "image", "media",
   "audio", "base64", or "binary", leave the wrapper as `-> Any`, note it in
   `session-overview.md`, and do not model a payload the probe never saw. A blob *resource*
   is different: the accessor returns the decoded `bytes` and the probe records `"bytes"` —
   step 4 still leaves it `Any`.

   **Empty-store probes produce under-typed list fields.** A read tool returning `[]` leaves the
   element shape unobservable and the skeleton omits the field; add `"<field>": "list"` by hand to
   keep it visible, never a guessed element type. Note in `session-overview.md` that it is
   unobservable at probe time — when the empty list is the records themselves, as an `empty
   store` line (step 2) — and recommend re-probing once the server holds representative data.

   **Bootstrapping sample args.** Some tools need a real id first (e.g. before probing
   `get_entity`). Find a no-arg / discovery tool on *this* server that returns user or entity ids
   — there is no universal tool for this, infer from `mcpgen list` output. Capture its raw
   payload in the same invocation that probes it:

   `<mcpgen> probe <server> <discovery-tool> --args '<args>' --save-raw <server>.<discovery-tool>.probe-raw.json`

   then read the ids from that file. `--save-raw` requires a `*.probe-raw.json` name (git-ignored)
   and saves the duplicate live round-trip that a separate `mcpgen call --out` costs. **No
   `--emit-shape` here** — the discovery tool is scaffolding, and emitting a part would merge it
   into the committed shape spec permanently. The skeleton goes to stdout instead. Use
   `mcpgen call --out` only when you do not want a shape at all.

   **`probed_args` carries live PII.** Batch agents write parts with raw args; `mcpgen merge`
   scrubs them on the way into `<shapes-path>`, and the step-3 ignore preflight covers the parts
   themselves. A template's variables are its `probed_args` and are scrubbed the same way.
   **Entry keys are not:** a static resource's URI is its key and reaches the committed
   `<shapes-path>` verbatim, and it cannot be redacted without breaking the match. Do not probe
   a static whose URI embeds a user, email, or record id; skip it in `session-overview.md`
   instead.

3b. **Consolidate parts → shapes.json.**

   ```
   <mcpgen> merge <server> --out <shapes-path>
   ```

   **`--out <shapes-path>` is required when `<shapes-path>` is not in CWD** (e.g. a subfolder).
   It must exactly match the `--emit-shape` value from step 3 — this is how the tool locates the
   parts directory (`<shapes-path>.parts/`).

   Merges all part files into `<shapes-path>`, then removes the parts directory. Run once after
   all probes finish.

   - Existing entries for tools that were **not** re-probed are preserved, so hand edits survive
     partial re-probes.
   - Parts for re-probed tools overwrite the corresponding base entry.
   - `--keep-parts` retains the parts directory for inspection.
   - `mcpgen codegen` also reads parts directly (in-memory merge) if the merged file is absent
     **and `--shapes` was not passed** — the explicit flag takes the file as given and does not
     fall back. So you can skip 3b during rapid iteration only while relying on auto-detect. Run
     it before committing either way, so the repo contains a single hand-editable artifact.
   - Also emits a gitignored `<shapes-stem>.verify.json`. The sidecar name comes from the
     `<shapes-path>` filename with `.shapes.json` stripped, not from `<server>`, so it always
     lands beside the shapes file. It holds a flat `{tool: probed_args}` map sourced from raw
     parts (pre-scrub) for the roundtrip verifier. Partial re-probes overlay existing entries —
     except a re-probe with empty args, which does not overwrite: delete that tool's stale entry
     by hand, or the verifier replays the old arguments live. A template's entry is keyed by
     the template string and holds its variables; a static resource takes no args and gets
     no entry.
   - Entries carried forward unprobed are checked against the manifest beside the shapes file
     (`<shapes-stem>.mcpgen.json`): tools against its tools, resource entries against its
     resource sections. Those absent from it are named as possibly retired. A manifest
     without the matching section (written before 0.11.0, or by a narrower `--resources`)
     cannot say, so merge prints only a count — `N resource entry(ies) not checked`.

4. **Edit the shape-spec — THIS is the judgment.**

   **First: review the scrubbed `probed_args`.** `mcpgen merge` has already replaced emails,
   UUIDs, home-directory usernames and long numeric ids, and flagged each changed entry with
   `probe_args_scrubbed: true`. Read what remains and redact anything the patterns cannot
   recognise — personal names, hostnames, bespoke internal ids. See the PII-vs-functional rules
   in `references/shape-spec.md`, which also documents every field below.

   Then, for each tool entry, set `unwrap`, `return_model`, `return_container`,
   `input_overrides`, `fields`, and `source`; delete `_observed_shape`; and add
   `_mutating_suspect` / `_mutating_reason` for every probed tool step 2b flagged.

   **Resource entries** take the same fields with three differences — the "Resource entries"
   section of `references/shape-spec.md` has the detail:
   - Leave the key and `kind` as the probe wrote them; codegen matches on both.
   - `input_overrides` is keyed by the template's **raw** variable names, and there is no
     `discriminator` or `variants` (codegen ignores them with a warning).
   - **A blob stays `Any`.** An `_observed_shape` of `"bytes"` gets no `return_model` and no
     `fields`: the accessor returns the decoded `bytes` at runtime, and the shape-spec has no
     key to annotate a bare scalar return.

   **Discriminator resolution is mandatory for polymorphic-suspect tools.** For every tool
   flagged in step 2.e **that is in the selected set**, choose one of three options before the
   shape-spec is considered complete; a flagged tool outside the set stays recorded as
   unresolved. Default: probe all variants (≤20).

   1. **Probe all variants** *(default, ≤20 values)* — emit `discriminator` + `variants`; for
      list tools keep `return_container: "list"` so each overload returns `list[<Variant>]` and
      the impl returns `list[V1 | V2 | …]`. Codegen supports that combination directly — no
      manual edits needed.

      **Needs a scalar discriminator.** An array (`sources: ["web", "news"]`) is not one:
      overloads key on `Literal['<value>']`, so the values have to be mutually exclusive, and an
      array can request several at once and get every matching key back. No set of overloads
      describes that, so nothing renders — resolve it with option 2 or 3, however cleanly its
      variants probed. An *optional* discriminator is fine: codegen emits an extra overload for
      the omitted case, returning the union.
   2. **Generic base model** — a shared `TypedDict` of fields common to all variants
      (`total=False`), when variants are many/unstable or precision isn't worth N live calls.
   3. **Unwrap-only / `Any`** — when values can't be enumerated and a base model isn't justified.

   Use `AskUserQuestion` when unsure which applies.

   > **Non-interactive fallback — when 2d's condition is not met *and* the variants were not all
   > probed** (more than 20 values, or values step 2.e could not enumerate): default to a
   > **generic base model** (option 2), and to unwrap-only `Any` only when variants are too
   > diverse or structurally incompatible. Where every variant *was* probed and the
   > discriminator is scalar, option 1 stands: emit the variants. Either way,
   > never emit a variant-specific `return_model` from a single-variant probe alone.

5. **Regenerate.** Reach the server exactly the way step 1 did — one form, not both:

   ```bash
   # config form: the env prefix is part of the command, not an argument
   MCPGEN_SERVERS=servers.json <mcpgen> codegen <server> --out <server>/<server>.py --embed-schema
   # direct form: substitute step 1's own --stdio / --url (+ --bearer) values
   <mcpgen> codegen <server> --stdio "<launch command>" --out <server>/<server>.py --embed-schema
   # non-default shapes filename only: add --shapes <path>
   ```

   Never combine the two: `--stdio` outranks a config entry, so a command carrying both silently
   reaches a different server than step 1 probed.

   No `--shapes` is needed in the layout this skill uses. Auto-detection looks for
   `<server>.shapes.json` — built from the **server name**, not from the `--out` filename — in the
   `--out` directory, which is exactly where step 3b wrote it. For a URL-form server argument the
   stem is only the host, so the name will not match the paths you built from the URL: pass
   `--shapes` explicitly there. Two constraints when you do: it takes the file, never the
   `.parts/` directory, and passing it disables the in-memory parts fallback, so
   `codegen --shapes <path>` on a not-yet-merged run fails with `FileNotFoundError`.

   Now shaped tools and resource accessors return their `TypedDict` (or `list[<model>]`),
   unwrapping via `_dig` / `_dig_list`.

   Add `--resources templates` or `--resources none` when step 2b narrowed the resource scope;
   the manifest `codegen` writes records the scope, so `mcpgen check` needs no flag.

   Read `codegen`'s stderr for two resource warnings. `shapes entry '<key>' matches no listed
   resource template — not applied` means the key differs from the listed URI or template:
   rename the key to the string `list --resources` printed, in both `<shapes-path>` and
   `<shapes-stem>.verify.json`. `has unknown kind` means `kind` was edited to something other
   than `resource` or `resource_template`.

6. **Verify.** `ast.parse` the module; confirm the eval target — the shaped tool's signature
   reads `-> Entity` (not `Any`) and its body digs the envelope. Where a hand-built wrapper
   exists, diff the generated unwrap against it as an oracle. For each shaped resource, confirm
   the accessor the same way: `read_dimension_by_unit_type(caller, *, unitType: str) ->
   list[Dimension]`.

   A text match on `-> Entity` passes even when the wrong model is named, so also check the
   models against the annotations: every `class <Name>(TypedDict, …)` in the module must appear
   in at least one function's return annotation. A class nothing returns means a wrapper is
   annotated with the wrong model. If `codegen`'s stderr printed `shape collision for '<Name>'
   (<owner>); emitted '<Name>_2'`, two entries share a name with different `fields`: rename one
   in `<shapes-path>` (see `references/shape-spec.md`) and rerun `codegen`.

7. **Generate a smoke-test runner.**

   Once the wrappers are shaped and verified, invoke `/mcp-client-kit:generate-mcp-runner`.
   Generation adds one new file, so it is the default in every non-interactive run — a subagent
   that cannot reach `AskUserQuestion` generates the runner rather than skipping the step.

   When the output folder already holds a `run.py`, leave it alone and report that it exists;
   a hand-edited runner is not regenerated over.

   > **Interactive exception:** where 2d's condition holds, offer the choice first
   > (single-select, 2 options: **Yes** — invoke `mcp-client-kit:generate-mcp-runner` now;
   > **No / I'll do it later** — stop here) and honor the answer.

   Pass the following so the runner skill does not re-derive them:

   - **The read-only tool set** — name the tools the runner may call and the ones it must skip;
     this verdict overrides the runner's own classification. Pass it explicitly: the runner
     re-derives mutating tools from a fixed keyword list narrower than step 2b's — it lacks
     `add`, `append`, `insert`, `upsert`, `push`, `close`, and `revoke`, and reads neither
     `annotations` nor the description semantically — so a tool only step 2 catches would
     otherwise be called for real.
   - **The resource accessors** — the `read_*` functions the module holds. They are
     read-only, so the runner includes them by default; name the data-like statics step 2b
     skipped so it leaves their accessors out. Name any tool whose description says to read
     a resource first, so the runner orders that accessor ahead of it.
   - **Resolved `mcpgen` path** — the absolute path step 0 printed. Pass it explicitly: the
     runner tries it as its first candidate, so one `--version` call settles its check instead
     of a fresh probe down the same three forms.
   - **Server name** — `<server>`.
   - **Output folder** — the dir from `--out` (e.g. `<server>/`), holding `<server>.py`,
     `<shapes-path>`, and `<shapes-stem>.verify.json`.
   - **Connection source** — exactly how step 1 reached the server: the `servers.json` path used
     via `MCPGEN_SERVERS=` / `--config`, **or** the direct params `--stdio "<launch>"`,
     `--url "<url>"` (+ `--bearer "$ENV_VAR"`).
   - **Transport + auth kind** — the `(transport, auth_kind)` tuple, e.g. `(http, oauth)`,
     `(stdio, none)`, `(http, bearer)`.

   That skill reads the module, `shapes.json`, and `verify.json` you produced and authors a
   workflow-ordered, shape-aware smoke test in one step. This step is a pointer only — do not
   duplicate the runner procedure here.

## Guards (do not violate)

- **Only mcpgen talks to the server.** Every live interaction — `list`, `probe`, `call`,
  `read`, bootstrap, inspect — goes through `mcpgen`. Never shell out to `curl`, `gh`,
  `httpie`, or raw `python` HTTP. mcpgen owns auth (browser OAuth + silent token refresh);
  any other client is unauthenticated, leaks the bearer token, or both. Need a raw payload?
  That's `<mcpgen> call … --out *.probe-raw.json`, or `<mcpgen> read … --out
  *.probe-raw.json` for a resource (both git-ignored).

- **Never pipe any `mcpgen` command that talks to a server.** Stdout carries the artifact —
  `list` prints its JSON there, `codegen` the module whenever `--out` is absent — so a pipe over
  it truncates or discards the very thing you ran the command for. The status misleads on top of
  that: a pipeline reports the last command's status unless `pipefail` is set, so
  `… 2>&1 | tail` hands back `tail`'s exit code and a failed run reads as success. Nor does
  `pipefail` make `2>&1` safe: the `mcp-remote` bridge logs its handshake to stderr and the
  `uvx`/`npx` launchers add install noise, all folded into what you read back. Keep the streams
  apart — send **stderr alone** to a file: `<mcpgen> codegen … 2>codegen.log`, then read it
  separately.

- **Run one `<mcpgen> call` per shell invocation.** Give two chained calls the same `--out` — the
  per-tool `<server>.<tool>.probe-raw.json` naming exists to stop exactly that — and the second
  overwrites the first, leaving only whichever call last succeeded, with nothing in the file to
  say which that was. A failing `call` writes nothing at all, which leaves that call's file
  either missing or holding an earlier run's payload — and a chain that exited 0 gives you no
  reason to look. The one failure that does write is a result the tool flagged `isError`: its
  body lands in `--out` and `call` still exits 1. If you must chain, give each call **both** its
  own `--out` and a per-call status label. (Independent `probe` invocations still batch — see
  step 3.)

- **Bound a slow `mcpgen` call with the harness's own timeout, never a `timeout` binary.**
  `probe` and `call` bound each tool call with `--timeout` (default 60 s), but not the connect
  and `initialize` handshake, which an interstitial page served with HTTP 200 stalls
  indefinitely; that case still needs the harness's timeout. The bound is per call, so a batch
  of N probes, or one probe with several `--args`, can run N × `--timeout`: set the harness's
  command timeout to at least the sum of the `--timeout` values in the command, or lower
  `--timeout` for the batch. Nothing guarantees a `timeout` binary is installed: macOS ships
  none by default, and there the wrapped command never runs at all — the whole line dies with
  `command not found` and status 127, which reads as the command having failed rather than
  never having started.

- **Probing is a live call — mutating tools mutate.** Probing a mutating tool executes it for real
  against the server. Classify per `references/mutating-tools.md`, keep flagged tools out of the
  selected set unless the user explicitly opts one in, and never probe a destructive tool "to see
  its shape" without explicit confirmation.

- **The type is a hint, not validation.** A `TypedDict` from one probe is partial knowledge stated
  honestly. Don't reach for Pydantic to "enforce" it — a model built from one response rejects
  valid variant responses with false authority. Zero runtime cost and zero dependency is the
  point; generated wrappers stay importable anywhere (the seam principle).

- **Don't model depth from one probe.** A container is promoted no further than its own
  annotation: `list[str]` / `dict[str, str]` where every element or value the probe evidenced
  is that same scalar, bare `list` / `dict` otherwise — a list needs its `--save-raw` payload
  checked element by element, since every probe's skeleton samples only the first element and a
  second probe adds no evidence. `dict[str, Any]` claims nothing `dict` does not. There is no
  nested `TypedDict`; see `references/shape-spec.md`.

- **Never emit a variant-specific `return_model` from a single-variant probe.** If a tool takes a
  discriminator arg (flagged in step 2.e), every sibling sharing that arg is polymorphic-suspect
  until probed across values or resolved to a base model / `Any`. Typing a tool `list[Person]`
  from one `entityType` value misdescribes every response the other values return.

- **Scalar enum params render as `Literal[...]` automatically** — do not hand-widen them to `str`.
  A param whose `inputSchema` pins scalar values — an `enum`, a `const`, or an `anyOf`/`oneOf`
  of `const`/`enum` branches plus an optional null branch — gets one with no flag required. A
  float in an `enum`/`const` falls back to the declared type; a union with a float or an open
  branch falls back to `Any`. Widen to `str` only if the server actually accepts values
  outside the declared set.

- **Review `probed_args` before committing.** `mcpgen merge` scrubs the committed
  `<shapes-path>` automatically; step 4 is where you catch what its patterns cannot recognise.
  Parts (`.parts/`) and `<shapes-stem>.verify.json` are gitignored raw counterparts; the only
  committable artifact is a fully-scrubbed `<shapes-path>`.
