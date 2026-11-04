# Changelog

## [Unreleased] — 0.12.0

### Changed

- **A one-shot `McpBridgeCaller.call` raises a call error bare.** Outside `connected()`, a
  JSON-RPC error from `tools/call` — an unknown tool, invalid arguments, an expired
  `call_timeout` — left the session's anyio task groups and reached the caller as
  `ExceptionGroup(ExceptionGroup(McpError))`, so `except McpError` around a generated wrapper
  missed it. `call()` now runs through the same path as `call_tool_result()` and
  `read_resource()` and raises the `McpError` itself once the session has closed. **Breaking
  for code that caught the group:** catch `McpError` instead. Auth and transport failures
  while the session opens or closes are unchanged.

## [0.11.1] — 2026-11-03

### Fixed

- **Tool listing reads every `tools/list` page.** `list`, `codegen` and `check` read only the
  first page and ignored `nextCursor`, so a server that paginates its tools got wrappers and a
  manifest for the first page alone. They now follow the cursor to the last page, with the
  repeated-cursor and 1000-page guards the resource listings already had.

- **A listing page that never arrives no longer hangs the command.** `ClientSession` sets no
  read timeout, so a server that never answered `tools/list`, `resources/list` or
  `resources/templates/list` hung `list`, `list --resources`, `codegen` and `check`. Each
  page now gets 60 s; past that the command exits `1` with `[list] error: TimeoutError: MCP
  server sent no tools page within 60s` (or `could not list resources: …` for a resource
  list). The bound is per page, so a long listing that keeps answering runs to the end.

## [0.11.0] — 2026-11-03

### Added

- **Generated modules read MCP resources.** Servers put reference data their tools depend on
  into resources — a dictionary a tool's description says to read first, a template such as
  `dimension://by-unit-type/{unitType}` — and generated code could not reach any of it.
  `codegen` now lists resources in the same session as tools and emits one `read_*` accessor
  per static resource and resource template, after the tool wrappers. Names come from the
  URI, not the server's non-unique `name`: `risk://dictionary` → `read_risk_dictionary`,
  `dimension://by-unit-type/{unitType}` → `read_dimension_by_unit_type(caller, *, unitType:
  str)`; a name taken by a tool gets a `_2` suffix. Templates expand at runtime (RFC 6570
  levels 1–4, scalar values), and `?`/`&` variables are optional. Each docstring carries a
  stable `Resource: <uri>` or `Resource template: <template>` line. Text contents decode as
  tool text does (JSON, then a Python literal, then the string), a blob to `bytes`, several
  contents to a list, and no contents raise `ValueError`. `--resources {all,templates,none}`
  picks the scope; above 50 static resources `codegen` warns that the server may list its
  data as resources.

- **`McpResourceReader`**, a second Protocol in `mcpgen.seam` (exported from `mcpgen`):
  `async def read_resource(server, uri) -> Any`. Accessors take it as their first argument.
  `McpCaller` is untouched, so a custom caller keeps satisfying every module without
  accessors. `McpBridgeCaller` implements both, one-shot or inside `connected()`, where a
  resource read and a tool call share one session; a read error reaches the caller as a bare
  `McpError`, not an exception group.

- **`mcpgen list <server> --resources`** prints `{"resources": [...], "resourceTemplates":
  [...]}`, trimmed to `uri`/`uriTemplate`, `name`, `title`, `description`, `mimeType`, and
  `size`, so an icon's `data:` URI or vendor `_meta` cannot flood the output.

- **`mcpgen read <server> <uri> --out FILE`**, the resource twin of `call`: one text content is
  written verbatim, one blob as raw bytes, several contents as a JSON array. A template
  target is expanded from `--args`. Errors exit `1` with a one-line message.

- **`mcpgen probe <server> <target> --resource`** reads a resource through `read_resource`,
  the path the accessors take, so the part records the shape they return. A template target
  is keyed by the template string, with each `--args` supplying one read's variables; a
  static URI is keyed by its normalized form and takes no `--args`. Entries carry `kind`, a
  blob's shape is `"bytes"`, and `--save-raw` base64-encodes it. Resource shape entries
  accept `unwrap`, `return_model`, `return_container`, `fields`, and `input_overrides`
  (keyed by raw variable name), but no discriminators.

- **`mcpgen check` detects resource drift.** Manifests gain optional `resources` and
  `resource_templates` sections. An added or removed resource or template, or a `mimeType`
  change, exits `1`; a `name` or `description` change is an advisory. `check --resources
  {all,templates,none}` overrides the scope, which otherwise follows the manifest's
  `resource_scope` — written by `codegen --resources templates` or `none` — or, without one,
  the sections it tracks. `--update` writes the sections it listed. An explicit `--resources`
  is recorded — `templates` or `none` writes `resource_scope` and drops the sections outside
  it, `all` removes the key; without it a recorded scope and the stored sections are kept.

- **Both skills handle resources.** `generate-mcp-wrappers` lists them in step 2a, selects
  templates and API-like statics while logging data-like lists as skipped, probes with real
  variable values, and hands the accessors to the runner. `generate-mcp-runner` finds
  accessors by their `Resource:` docstring line, includes them by default with template
  variables from `verify.json`, and places one before any tool whose description says to
  read it first.

- **`probe` and `call` time out a tool that never answers.** A tool that waits for a human,
  such as Playwright's `browser_annotate`, hung `probe` forever. Both commands now bound each
  tool call with `--timeout SECONDS` (default 60; `0` waits forever); on expiry they exit `1`
  with `[probe] timeout: no answer within 60s (raise with --timeout)` (or `[call] timeout: …`)
  and write no part file or `--out` file. `codegen --probe` takes the same flag. The bound is
  the SDK's per-request read timeout, so the session still closes normally and a stdio
  server's process tree is still terminated; the server is not told to cancel the call. It
  does not cover connecting or the `initialize` handshake, nor a `probe --resource` read,
  which notes on stderr that an explicit `--timeout` does not apply. `codegen --probe` also
  reports a tool error in one line instead of a traceback. In code it is
  `McpBridgeCaller(call_timeout=...)`; the default `None` leaves generated wrappers unbounded.
  The `generate-mcp-wrappers` skill skips tools whose description says they wait for user
  input, confirmation or a pick in the browser as **interactive**, logged under
  `mutating-skipped`, and records a timed-out tool as unprobed with cause `timeout`;
  `generate-mcp-runner` leaves interactive tools out of `run.py`.

- **`McpBridgeCaller.call_tool_result(server, tool, arguments)`** returns the SDK's unparsed
  `CallToolResult`, `isError` included, for code that must tell a tool's reported failure
  from its answer; `call()` parses both alike. Session handling and `call_timeout` are
  `call()`'s, but a call error in a one-shot session reaches the caller as a bare `McpError`,
  not an exception group.

### Changed

- **Manifests gain resource sections on the next regenerate.** `codegen` defaults to
  `--resources all`, so every manifest gains `resources` and `resource_templates` sections on
  its next run. For a server with no resources, or without the resources capability, both are
  written as `{}`: tracked and empty. Against a server with resources the module also gains
  `read_*` accessors; for one without, the module is byte-identical. `format_version` stays
  `1` and earlier mcpgen versions ignore the new keys, so a pinned CI job does not break.
  `--resources none` keeps the module byte-identical; its manifest adds only
  `"resource_scope": "none"`.

- **`check` against a manifest with no resource section and no recorded scope** prints an
  advisory ("live resources are not tracked by the stored manifest; re-run with `--update` to
  track them, or with `--resources none --update` to stop listing them") and exits `0`. It
  reads only the first page of each resource list for this, with a 10 s timeout, and a
  failure there is an advisory too. Any other resource listing — a tracked manifest, a
  recorded scope, an explicit `--resources`, or `--update` — is strict, and a failure exits
  `2`. A manifest from `codegen --resources none` records the scope, so `check` lists no
  resources for it and prints no advisory.

- **Plain `mcpgen list` also asks for resources.** When the server declares the resources
  capability, `list` reads the first page of each resource list in the same session, bounded
  by a 10 s timeout, and prints a stderr hint such as `2 resource(s) and 1+ resource
  template(s) available`. Its stdout is unchanged, and a resource failure becomes a stderr
  note, never a failed `list`. An `McpError` from `tools/list` that arrives wrapped in an
  exception group now exits `1` with a one-line message instead of a traceback.

- **`codegen` reports an `McpError` as a one-line error and exits `1`** instead of a
  traceback. A failed resource listing adds a hint to retry with `--resources none`. A
  shapes entry that matches no listed resource or template, or has an unknown `kind`, warns
  instead of being dropped silently.

- **`merge` counts entries, not tools**, since shapes files now hold resource entries too
  (`wrote … (N entry(ies))`). Carried-forward resource entries are checked against the
  manifest's resource sections rather than its tools; where the section is untracked, merge
  prints a count of unchecked entries instead of flagging each one as a retired tool.

- **`probe` refuses to overwrite a part file that holds another key.** On a
  case-insensitive filesystem `Search` and `search` (or `demo://x/Users` and
  `demo://x/users`) share one part file, and the second probe used to drop the first
  silently. It now exits `1` before any live call and says to merge first. Keys too long for
  a filename are cut short and end in a sha256 suffix; tool part names are unchanged.

- **A lone `const` parameter is typed as a `Literal`.** `{"type": "string", "const": "x"}`,
  which rendered as `str`, and `{"const": "x"}`, which rendered as `Any`, both become
  `Literal['x']` on the next regenerate, so a caller passing any other value now fails type
  checking. A `null` or float `const` keeps the old type.

- **The plugin's engine floor rises to `mcpgen >= 0.11.0`** in both skills' step 0. The skills
  now instruct `list --resources` and `probe --resource`, which 0.10.0 does not have, and
  generated runners call `McpBridgeCaller.read_resource`.

- **`call` exits `1` on a result the tool flagged `isError`.** It still writes the error body
  to `--out`, as evidence, then prints `[call] tool returned isError: <text>` (the first 300
  characters of the error text, whitespace collapsed and credentials redacted) and exits `1`
  where it exited `0`. A result with no content, or a body `call` cannot parse or serialise,
  writes nothing. A script that read the `--out` file only after a zero exit now sees the
  failure.

- **`generate-mcp-wrappers` records why each tool went unprobed.** `session-overview.md` lists
  them under `## unprobed` as tool, cause and detail, the cause one of `id-gated` (with the
  blocking argument and the producer tool that would mint it), `entitlement-gated` (quoting
  the server's class, such as `tool_error_class: entitlement_required`), `empty store`,
  `tool error`, `timeout`, or the residue `inconclusive`; interactive and mutating skips stay
  under `## mutating-skipped`. Tools that needed an id, a plan the account lacks, or data the
  store did not hold were all logged alike as inconclusive.

- **A deprecated tool's fixed notice counts as observed.** A tool that returns the same prose
  whatever the args, such as a deprecation or "moved to …" notice, is a `NOT_JSON` observation
  with `_observed_shape: "str"` and an `-> Any` wrapper, noted as deprecated in
  `session-overview.md`, not an unprobed or inconclusive tool.

- **Both skills resolve `mcpgen` to one absolute path.** Step 0 turns each candidate —
  `mcpgen` on `PATH`, `uv run mcpgen`, `.venv/bin/mcpgen`, and the runner's handed-over value —
  into the path of the executable it would run before checking the version, and later blocks
  paste that path in for `<mcpgen>`. Agents that kept `uv run mcpgen` in a shell variable ran
  every probe of a batch wrong under `zsh`, where an unquoted `$c` does not word-split.
  `session-overview.md` now also records a failed batch or command, with its error, when a
  retry succeeded.

### Fixed

- **A union-typed input param no longer crashes `list`.** A param declared
  `"type": ["string", "null"]` — legal JSON Schema, emitted by real servers for a nullable
  arg — raised `TypeError: unhashable type: 'list'` in discriminator detection, which
  `mcpgen list` runs on every tool, so one such param broke listing for the whole server.
  Such a param is now skipped and never becomes a discriminator candidate.

- **`probe` no longer records a tool's error as its shape.** A tool reports its own failure
  as a result flagged `isError` — Notion answers a tool its plan lacks with
  `{"code": "validation_error", …, "additional_data": {"tool_error_class":
  "entitlement_required"}}` — and `probe` parsed that body, recorded its keys as
  `_observed_shape` and exited `0`, bait for a `TypedDict` of the error. `probe` and
  `codegen --probe` now exit `1` with `[probe] tool returned isError: <text>` (`[codegen] …`),
  the first 300 characters of the error text, whitespace collapsed and credentials redacted,
  and write no part file, `--save-raw` file, module or manifest. A result with `isError`
  false or absent is parsed as before. `McpBridgeCaller.call`, and so every generated
  wrapper, still returns an `isError` result's content like any other. The
  `generate-mcp-wrappers` skill records such a tool as unprobed, citing the printed text,
  and keeps `return_model: null` shapes for error bodies a tool does not flag.

- **`call` reports a tool error in one line.** A server-side `McpError`, or any failure that
  escaped the session as an exception group, printed a group traceback. `call` now handles it
  as `probe` does: `[call] error: <cause>` and exit `1`, auth failures through the usual
  re-login hint, and still no `--out` file.

- **A second probe no longer counts as evidence for `list[T]`.** Plugin 0.5.0's
  `generate-mcp-wrappers` skill let a `fields` entry claim `list[str]` on the strength of the
  saved raw payload *or* a second probe. `summarize_shape` samples only the first element of
  every probe, so `['a', 7]` and `['b', {}]` both summarize as `['str', '...x2']` and two
  probes can agree on an element type neither list has. The skill and `shape-spec.md` now
  require the `--save-raw` payload, checked element by element; without it the field stays a
  bare `list`.

- **A renamed return model now reaches the wrapper that caused the rename.** When two shape
  entries shared a `return_model` name with different `fields`, `codegen` emitted
  `KnowledgeGraph_2` but still annotated and cast the second wrapper to `KnowledgeGraph`, so
  the new class went unused and the wrapper promised the wrong keys. Tools, discriminated
  variants and resource accessors now return the class emitted for them, and an entry whose
  fields match an existing `_N` model reuses it instead of emitting another. The collision
  warning names the tool, variant or URI involved. Resources made this easier to hit: a tool
  and a resource serving the same entity often pick the same name.

- **A closed `anyOf`/`oneOf` parameter is typed as a `Literal`, not `Any`.** Zod and pydantic
  express a value set as a union of `const` branches, or as pydantic's
  `Optional[Literal[...]]`: `anyOf: [{"type": "string", "enum": ["a", "b"]}, {"type":
  "null"}]`. `codegen` punted every union to `Any`, so such a parameter rendered as `policy:
  Any | None = None`. A union whose branches each carry a `const` or a non-empty `enum`
  (whatever other keys sit beside it) or are `{"type": "null"}` now renders as
  `Literal['a', 'b']`, plus `| None` when a branch allows null. A union with an open branch,
  a float value, or any `allOf` stays `Any`.

- **Generated modules import exactly the `typing` names they use.** The `Literal` import was
  gated on a top-level `enum` or one inside `items`, so `list[list[Literal[...]]]` and a
  `Literal` written into a tool's or resource template's `input_overrides` or a model's
  `fields` referenced `Literal` without importing it, while a float-only `enum`, which renders
  as `float`, imported it unused. A discriminator entry imported `Literal`, `TypedDict`,
  `cast` and `overload` even when no `@overload` was emitted. `codegen` now reads the names
  off the rendered code: `Literal`, `TypedDict`, `cast` and `overload` are imported only when
  used as bare names, so a docstring, `typing.Literal[...]` or a tool parameter named `cast`
  does not count. `Any` is always imported.

- **A field seen only as null is typed `Any | None`, not `None`.** A single probe records a
  null as `"NoneType"` in `_observed_shape`, and for a tool with an `unwrap` path the `fields`
  are written by hand from it, so `"NoneType"`, `"None"` or `"null"` reached `codegen`, which
  rendered `x: NoneType` (an undefined name) or `x: None` (a key that rejects every real
  value). `codegen` now rewrites a `fields` value that admits only None (`None | None` and
  `Optional[None]` too, and JSON null) to `Any | None` and lists it among the normalized type
  strings on stderr; `T | None` is left alone. `NoneType` inside an annotation, as in
  `list[NoneType]`, becomes `None`. A discriminator variant's `fields` and `return_model` are
  now normalized too; they were skipped. So a variant model that differed from another model
  only in these spellings now shares its name instead of being emitted again with a `_2`
  suffix.

## [0.10.0] — 2026-09-09

### Added

- **`mcpgen probe --save-raw FILE`** writes the untruncated payload beside the shape
  skeleton. `--emit-shape` records only what `summarize_shape` keeps — the first element of a
  list plus an `...xN` sentinel — so a list's element type could not be confirmed from a
  single probe, and the shape spec had to fall back to a bare `list`. The raw file settles it.
  The name must end in `.probe-raw.json`, which `.gitignore` already covers: the payload is
  unredacted by construction and the suffix is what keeps it out of a commit. A multi-probe run
  writes a JSON array of `{args, raw}` so each response stays paired with the arguments that
  produced it.

- **`mcpgen merge --keep-parts`** retains the `.parts/` directory instead of removing it after
  consolidation. A default merge deletes the intermediates, which makes re-merging a no-op —
  so any flag that changes how merge writes `probed_args` has to be decided at probe time, not
  after the fact.

- **`mcpgen merge` cross-checks carried-forward entries against a tool manifest** and warns
  about shape-spec entries the server no longer advertises. Entries survive a merge by design,
  so a tool renamed or withdrawn upstream left a stale entry that codegen kept emitting against
  a tool that no longer exists. `--manifest PATH` overrides the default
  `<shapes-stem>.mcpgen.json`; with no manifest available the advisory reports a count rather
  than naming entries it cannot verify.

### Changed

- **`mcpgen merge` scrubs `probed_args` before writing `<server>.shapes.json`.** The shapes
  file is committed, and `probed_args` holds whatever the probe was called with — real
  addresses, account ids, and home-directory paths. Merge now replaces email addresses, UUIDs,
  a leading home-directory user segment (`/Users/<name>`, `/home/<name>`, `C:\Users\<name>`,
  including their `file://` forms) and runs of 8+ digits with `<email>`, `<uuid>`, `<home>` and
  `<id>`, and sets `"probe_args_scrubbed": true` on every entry it changed. Raw values survive
  in the gitignored `.parts/` intermediates and in the new gitignored
  `<shapes-stem>.verify.json` sidecar, which the roundtrip verifier reads first — so scrubbing
  does not cost verification. Two limits are deliberate and documented in
  `references/shape-spec.md`: only strings are touched, since retyping a numeric id as a
  placeholder string would mislead both `input_overrides` and the verifier; and an **epoch**
  timestamp is indistinguishable from a numeric id, so it is rewritten to `<id>` and must be
  restored by hand from `verify.json`. `--no-scrub` opts out, and only helps on a merge that
  still has its `.parts/` — reach for it via `--keep-parts` at probe time.

- **The supported Python floor is now 3.10.** Two 3.11-only constructs stood between the code
  and 3.10, both on error paths — so imports and the CLI worked on 3.10 while any failure
  raised the wrong exception. `_carries_interrupt` and `_describe` tested against the builtin
  `BaseExceptionGroup`; below 3.11 they now use the `exceptiongroup` backport, the same class
  anyio raises. The OAuth callback wait caught the builtin `TimeoutError`, an alias of
  `asyncio.TimeoutError` only from 3.11, so on 3.10 a timeout escaped unconverted instead of
  reaching the "browser never came back" message. CI runs 3.10 alongside 3.11-3.13; ruff and
  mypy target 3.10.

- **The plugin's engine floor rises to `mcpgen >= 0.10.0`**, stated once for the whole plugin
  in both skills' step 0. `generate-mcp-wrappers` now instructs `probe --save-raw`, which
  0.9.0 does not have.

- **`generate-mcp-runner` resolves the CLI instead of shelling out bare.** It gated on a bare
  `mcpgen --version` and ran a bare `mcpgen list`, neither of which is on PATH in a uv-managed
  project. Step 0 now reads the hand-off from `generate-mcp-wrappers` step 7 and treats the
  caller's invocation as the resolver loop's first candidate — a valid one settles the check in
  one `--version` call, an invalid one falls through rather than aborting, which is what covers
  a caller that gated nothing. Both resolvers now compare the release part of the reported
  version and reject a pre-release whose release part equals the floor exactly: `sort -V` alone
  orders `0.9.0.dev1` above `0.9.0`, and a clone parked at a pre-release can predate the commit
  the floor exists for. A newer pre-release still passes.

- **The shape-spec `fields` rules now say when a container carries its element or value type.**
  `list[str]` and `dict[str, str]` are legal where the evidence covers every member — dict
  evidence is complete from one probe, list evidence is not — and bare `list` / `dict`
  otherwise. The ceiling is stated outright: codegen emits a `TypedDict` only for a name in
  `return_model` or a `variants` entry, so a nested model name renders an annotation with no
  class behind it and mypy fails on a module that still imports. `dict[str, Any]` and
  `list[Any]` say nothing `dict` and `list` do not, and are prohibited.

## [0.9.0] — 2026-08-27

### Fixed

- **An optional discriminator no longer becomes mandatory.** `_render_overloaded` emitted the
  discriminator with no default whether or not `inputSchema.required` listed it, so a parameter
  the server treats as optional was forced on every call and forwarded even when the caller had
  not chosen a value. Nothing caught it: the parameters are keyword-only, so Python accepts a
  non-defaulted one after defaulted ones and the module parses cleanly — the only symptom was a
  call that used to type-check and no longer did. An optional discriminator now renders an
  additional overload covering its omission, which returns the variant union because the schema
  cannot say which variant the server picks by default; the impl takes `| None = None` and
  forwards the argument only when it is supplied. A discriminator listed in `required` renders
  exactly as before.

## [0.8.0] — 2026-08-26

### Fixed

- **`mcpgen login` could not authenticate against a server behind bot protection.**
  The OAuth handshake sent no `User-Agent` at all: `mcp.client.auth.oauth2` hand-builds
  its `httpx.Request` objects and yields them from the auth flow, and httpx does not merge
  client default headers into requests yielded that way — so metadata discovery and dynamic
  client registration went out bare and a Cloudflare filter answered them `403 Access
  Denied`, while the client's own requests succeeded. mcpgen now names itself
  (`mcpgen/<version> (python-httpx)`) through a request event hook, which is the one place
  that fires on both kinds of request, and on the token-refresh client's headers. A
  `User-Agent` supplied in a server's configured headers still wins.

- **A caller logging in on demand for a credential that never takes prompted every time.**
  `login()` reports success when the authorization server issued a token, which says nothing
  about whether the store kept it or the result is usable. Two failures follow from that and
  both loop: a store that holds nothing new afterwards — a backend that accepts the write
  without persisting it, an entry another process keeps clearing — and a token whose
  lifetime is too short to survive the freshness margin every read applies, which the next
  call treats as absent. Each browser round keeps succeeding and the condition that
  triggered it keeps being true. `ensure_login` now checks the store on the call that
  prompted and raises `LoginWontHelp` for either, so the loop stops at one prompt.

  On the `keyring` backend the "nothing new" message distinguishes a split store. A keychain
  `login()`'s own storage instance could not use — a write its ACL denied, or a read that
  failed there and not in the caller — flips that instance to the file mid-login, so the
  token lands in `credentials.json` while every keyring
  backed read — this instance and every later one, since `resolve_cred_backend` re-resolves
  and `_detect_keyring` never probes a write — still consults the keychain. The verdict is
  the same, because nothing will ever read that token and another browser round repeats the
  split; the message names the file and the two fixes rather than claiming the token was
  not kept.

  The same message stops short of claiming permanence when the *caller's* keychain read is
  what failed: this instance then drops to the file for the rest of its life while `login()`
  may have written the keychain successfully, so it reports that a retry can find what this
  check could not look at.

  The freshness test is the file's own: `get_tokens` and `_pre_flight_refresh` treat a
  token as absent from `expires_at - _MARGIN` onwards, so the check does too. `expires_at`
  is computed from the local clock at the moment of the write, never sent by the
  authorization server, so a credential failing this test is never a clock to correct.

  Inside that margin the verdict splits on the lifetime the token endpoint reported, which
  `_serialize_tokens` stores alongside the deadline. A lifetime at or under `_MARGIN` is
  reported whatever else is cached: the token is absent from the instant it is written, and
  a server handing those out on code exchange gives no reason to expect its refresh
  responses differ — where they do not, accepting would renew into another margin-dead
  token and open the browser on every call. A longer lifetime that the post-login check
  merely spent is reported only when nothing cached can renew it;
  where a refresh token, `client_id` and `token_endpoint` are all present, the next call's
  pre-flight renews out-of-band and the setup works, so it is accepted. Short access tokens
  paired with refresh tokens are a recommended hardening pattern, and blocking them on how
  long `initialize()` and `list_tools()` happened to take would be a false alarm.

  Checked on that call rather than remembered across calls, because later on the two are
  indistinguishable from an ordinary expiry: a revoked grant and a login that never took
  both present as "a token is present and the server refuses it". Comparing the store
  against its own before-state is only possible while that state is still known. A process
  that outlives its grant therefore still logs in again whenever it genuinely needs to.

  A login that *raises* is untouched — a cancelled consent screen, a callback timeout and an
  unpasted URL keep their own types and their retry. `mcpgen login` and a direct `login()`
  call do not run the check: it belongs to the automatic path, which is the one that can
  loop, and an explicit login should report what happened rather than police a store it had
  no say over.

## [0.7.0] — 2026-08-25

### Fixed

- **Two mcpgen processes writing credentials at the same time silently dropped one of the
  updates.** The store is a single JSON document keyed by server, so every write is a
  read-modify-write of the *whole* file: a process touching one server saves everyone's
  entry. With the read outside any lock, the second writer's snapshot predates the first
  writer's `os.replace`, and saving it puts the stale view back — the first server's
  freshly-issued token is gone, with no error anywhere, and its next run goes to the
  browser. Two halves needed closing. Corruption: both `FileTokenStorage._file_save()` and
  the client-config writer staged through a fixed `.tmp` path, so two processes wrote into
  one partial file; the staging name now carries the pid, matching the convention already
  used for generated files. Lost updates: whole-store cycles now run under an advisory lock —
  a sidecar `credentials.json.lock`, `flock` on POSIX and `msvcrt.locking` on Windows —
  and, critically, **re-read the store inside the lock**, so no write is ever built on a
  snapshot older than the lock it is under. `delete_cred` was the sharpest edge: it clears
  the entire store when its view says the entry it removed was the last one, so a login
  landing in that window had its brand-new credential unlinked by a command that was never
  asked to touch it. Also covered: `set_tokens`, `set_client_info`, the token-endpoint
  cache, `login()`'s stash and restore, and `migrate-creds` — whose merge and purge are now
  one operation rather than four races. Both primitives are released by the OS when the
  holder's descriptor closes, so a killed process leaves nothing stale to break or time out.

  The lock set follows the *backend*, not the path — a fix covering only the file backend
  would have left half the surface open. The keyring is one global item, and `--creds` is
  documented as ignored there, so two keyring processes carrying different paths would
  otherwise lock different sidecars around the same document and exclude nothing. The
  keyring backend therefore takes a fixed `~/.mcpgen/keyring.lock` *and* the path sidecar,
  always in that order (the second is not redundant: a keyring failure falls back to
  writing the file, and that write has to be covered too).

  Three visible consequences. Keyring-only users now get empty sidecar lock files (0600,
  holding nothing) under `~/.mcpgen`, the first files mcpgen writes there on that backend.
  A lock that cannot be created or acquired reports on stderr and proceeds *unlocked*
  rather than failing the operation — no lock is a lost update under concurrency, while
  raising would be a failed credential write on a path that worked before (it is not a
  `warning`, so `-W error` cannot turn that degrade back into the failure it avoids). And
  the lock is taken before the read that decides whether anything needs writing — that
  ordering *is* the fix — so deleting an absent credential, or migrating an empty source,
  now leaves a directory and an empty lock file where both were previously filesystem
  no-ops. Taking a lock never changes the permissions of a directory it did not create;
  saving credentials still hardens the directory to 0700, as it always has.

  Three limits. `login()` does **not** hold the lock across the browser round — one person
  clicking through a consent screen would stall every other mcpgen process for as long as
  they take — so two concurrent logins for the *same* server remain last-writer-wins; the
  restore's re-check bounds the damage but does not remove it. The lock is advisory and
  mcpgen-to-mcpgen: another program writing the same keyring entry is not coordinated, and
  cannot be. And on Windows the primitive is `msvcrt.locking(LK_LOCK)`, which is not a
  blocking acquire but a bounded one — about ten retries a second apart, then `OSError`,
  which is the "cannot be acquired" case above and therefore proceeds unlocked with a line
  on stderr. Contention lasting longer than ten seconds there is the pre-fix behaviour, and
  `migrate-creds` is the one operation that holds the lock long enough to reach it. POSIX
  has no such ceiling: `flock` waits. The 0600 sidecars are likewise a POSIX statement —
  the mode bits are written on Windows too, and NTFS ACLs are what actually decide.

- **A credential that arrived during a token refresh could be overwritten by the refresh
  that was already in flight.** No lock can span this one: `_pre_flight_refresh` reads a
  refresh token, `await`s an HTTP round-trip, and writes what comes back. A login landing
  inside that window was replaced by a response chained to the refresh token the request
  had *started* from — and where the authorization server rotates refresh tokens, that
  chain is already invalid, so the overwrite cached a dead credential and sent the next run
  to the browser. It now compares and sets: the response is stored only while the store
  still holds the refresh token it was derived from. A credential deleted meanwhile is not
  resurrected either, for the same reason. `login()`'s restore does the same check over its
  own, much longer window.

- **A refresh response that left out `refresh_token` erased the stored one, so the next
  expiry had nothing to refresh with.** RFC 6749 §6 makes that member optional in a
  refresh response and says to discard the old token only when a *new* one is issued;
  Google's token endpoint omits it. mcpgen replaced the whole cached entry with whatever
  came back, so on a server that does not rotate, the refresh token vanished on the first
  successful refresh and every token lifetime after that ended in a browser prompt.
  Both writers now carry the stored refresh token forward when the response has none.
  A rotated one still replaces it, as §6 requires; revocation is unaffected, because a
  revoked grant arrives as `invalid_grant` on next use, which is already read as dead.

- **A brief outage at the authorization server sent every run to the browser for a login
  that could not fix it.** `_pre_flight_refresh()` turned *any* non-200 from the token
  endpoint into `ReauthenticationRequired`, which `ensure_login()` converts straight into a
  browser prompt — so a `502`, a `503`, a `429`, or a Cloudflare error page in front of the
  authorization server produced an interactive re-login that then asked the same unreachable
  host for a token. Across a batch that is one impossible prompt per item. `httpx` transport
  errors escaped unclassified entirely, so callers could not branch on them at all. This is
  the 0.6.0 `login()` bug one layer up: there a completed login was discarded, here a live
  refresh token was declared dead because the endpoint that would renew it was briefly down.
  The failure is now classified by who answered and what they faulted, read from the RFC
  6749 §5.2 JSON `error` body rather than the status. The grant is treated as dead for
  exactly three codes — `invalid_grant` (the refresh token) and `invalid_client` /
  `unauthorized_client` (the registration, which `login()` replaces by dropping the cached
  `client_info` and re-running dynamic client registration) — on whatever status they
  arrive with, which covers a non-compliant server reporting a revoked grant as `403`.
  Everything else raises the new `TokenRefreshUnavailable` and leaves the refresh token
  untouched: a `5xx`, a transport error, a `408` from a proxy that never passed the request
  on, a `429`, a `temporarily_unavailable` or `server_error` code, a WAF block page, a `3xx`
  to a captive portal, a `404` from a moved endpoint, a `200` whose body is not a token, and
  the codes that fault the request rather than the credential (`invalid_request`,
  `unsupported_grant_type`, `invalid_scope`) — for those, a browser round produces a fresh
  token and then resends the identical bad request. §5.2 requires the `error` body on every
  `400` and `401`, so a bare or HTML one is a proxy rather than the authorization server.
  Nothing that stays out of the browser path
  is a dead end: every message whose cause is ambiguous enough that a fresh registration
  could still be the fix names `mcpgen login <server>` as the manual next step. A `200` is
  decided by whether it parses as a token, so a server that pads a good response with a
  blank `error` member still refreshes, and one that reports failure in-band with a `200`
  (Slack's rotation endpoint) is still classified rather than swallowed.

- **A refresh response that failed to parse printed the token it contained to stderr.** A
  `200` from a token endpoint *is* the token response, so the near-misses that fail
  validation — a `token_type` outside the `Bearer` literal, a non-integer `expires_in`, a
  rotation response with no `access_token` — carry a live `access_token` and
  `refresh_token`. The error naming that failure is printed by `mcpgen login` and by the
  `generate-mcp-runner` template, i.e. into CI logs. Response bodies quoted in an error now
  go through a redaction pass that drops `access_token`, `refresh_token`, `id_token`, and
  `client_secret` values in both JSON and form-encoded bodies, at any nesting depth and
  whatever casing or word separator the responder spelled them with — `accessToken` from a
  serializer left on its defaults and `access-token` from a kebab-case house style are the
  same credential as the RFC's spelling, and the responders that reach this code are exactly
  the ones re-serialising through a convention of their own — Slack wraps the token
  in `authed_user`, and a body a proxy truncated mid-response no longer parses as JSON at all
  yet still carries one — and the pydantic error — which quotes the
  whole input back on a `missing` field — is reported by type rather than by message.
  Everything else in the body is kept: `error`, `error_description`, and block-page text are
  what make the message worth printing.

  Redacting the message was not enough on its own, because the two raises on that path
  chained the pydantic error with `from exc`, and a chain travels with the exception to
  every caller. Only `mcpgen login` caught these types; on every other command the
  interpreter printed the whole chain — including the quoted body — to stderr, which is the
  CI log this redaction exists for. Both now raise `from None`. What that costs is the
  per-field pydantic detail, and the redacted body already shows the offending non-secret
  members; what it buys applies to library callers and the generated runner too, neither of
  which any CLI-side catch could have covered. (pydantic truncates the quoted value, so what
  used to escape was a prefix of the credential — a smaller leak, not a different one, and a
  short secret escaped whole.)

- **The same credential leak was open on the login path, where the SDK does the parsing.**
  Closing it in `_pre_flight_refresh` covered the refresh only. `login()`'s post-login check
  goes through the MCP SDK, which reports a token or registration response that fails
  validation as `OAuthTokenError(f"Invalid token response: {pydantic_error}")` — and that
  text quotes the rejected body back as a *Python repr*, single-quoted. Neither existing
  pattern could see it: one needs a double quote, the other an `=`. `_describe()` put it
  straight into `PostLoginCheckFailed`, and `cli.py` printed that to stderr, so a gateway
  that camelCased its members turned a failed post-login check into a live refresh token in
  the CI log. Registration is the same shape with a longer-lived secret — RFC 7591 responses
  carry `client_secret`, which never expires on its own.

  Redaction is now one helper over four spellings, shared by `_describe()` and
  `_body_excerpt()` so they cannot drift, and both raise sites drop the chain. The fourth
  pattern drops pydantic's `input_value=` frame whole rather than scrubbing it member by
  member, because member-wise redaction is unreliable there by construction: pydantic
  truncates the quoted repr in the middle, and the cut lands mid-*key* as readily as
  mid-value — `{'accessToken': 'SECRET1'...efreshToken': 'SECRET2'}` is real output, where
  the second key has lost the `r` that any pattern would match on and its value is intact.
  Where the cut falls depends on the total length, which the server controls, so anything
  key-anchored closes one offset and leaves the rest. What survives the frame is what was
  ever diagnostic: the field name, the error type, `input_type`. That fourth pattern is
  scoped to *our own* exception messages and does not run over response bodies — a body
  never carries a frame this module produced, and an authorization server that puts
  `str(validation_error)` in its own `error_description` would otherwise lose the rest of
  that line to a pattern with nothing to do there.

- **Only `mcpgen login` handled an auth failure; every other command printed a traceback.**
  `codegen`, `probe`, `call`, `list` and `check` caught `(FileNotFoundError, ValueError)`,
  and the auth taxonomy is neither — so an expired credential or an unreachable token
  endpoint, both routine, ended in a stack trace. `main()` now catches the two roots,
  `ReauthenticationRequired` and `LoginWontHelp`, prints the message and exits 1. Catching
  the roots rather than widening each command's `except` covers the subclasses and whatever
  command is added next. It stays narrow deliberately: a `KeyError` from a real defect must
  still reach the interpreter, so it is reported as a bug rather than dressed up as an
  operational condition. `login`'s own handler runs first and keeps its wording.

- **A form-encoded rejection was never classified, so a dead grant on GitHub-style servers
  never prompted for a login.** The refresh request now sends `Accept: application/json`,
  which is what most servers need to answer in the shape §5.2 describes — but `Accept` is a
  request, not a guarantee, and a server that answers form-encoded anyway (GitHub's token
  endpoint does by default) still presented every rejection, `invalid_grant` included, as a
  body with no OAuth error code in it: an unidentified proxy response, permanently
  unrecoverable for a headless caller. The error code is now also read out of a body
  *labelled* `application/x-www-form-urlencoded`. The label is the whole point: an HTML block
  page containing the text `error=invalid_grant` is a body the authorization server did not
  send, and scraping it would manufacture the speaker evidence the classification turns on.
  An unlabelled form body therefore stays unclassified, which is what it did before, so the
  fallback adds no new way to be wrong. A body that parses as JSON is never re-read as a
  form, and a form body carrying `error` twice, or empty, is treated as carrying none —
  picking a winner would be a guess about which half the server sent.

- **A credential store that broke mid-login replaced the error the operator needed to
  see.** `login()`'s `except BaseException` handler re-reads storage to decide whether the
  flow got far enough to save a token; a corrupt `credentials.json` or a keyring backend
  that started failing raised from inside that handler, discarding the original transport
  error. An unreadable store is now treated as "nothing can be said about what was
  produced": the original failure propagates, and the previous credential — removed from
  disk when the flow began — is not written back, which means it is lost. That is the
  accepted half of the trade, not an oversight: writing blind onto a store that cannot be
  read risks clobbering whatever the unreadable bytes still hold, and when the choice is
  between one credential and every other server's, the store wins. The restore that follows
  it is guarded the same way —
  a store that refuses the *write* (a read-only filesystem, a keyring that has started
  refusing) would otherwise mask the original failure through the adjacent door.

- **A corrupt credential store broke the very command that repairs it.** `mcpgen login`
  reads the store before it writes, and that read was bare, so unparseable JSON — a write
  interrupted by a SIGKILL, a hand-edit gone wrong — met the one command whose job is
  producing a fresh entry with a raw `JSONDecodeError` and no route back but deleting the
  file. On the `file` backend `login()` now moves the unreadable file aside as
  `credentials.json.corrupt.<epoch-ns>`, says so on stderr, and starts from an empty store.
  The bad bytes are kept because they hold the other servers' entries; falling through to an
  empty store without them would let the next save erase credentials that were still
  recoverable by hand. If the file cannot be moved aside either — a permission problem, a
  lock — `login()` raises `LoginWontHelp` and stops, for the same reason: continuing would
  save an empty store over the bytes the quarantine exists to keep. It is the bare base class
  deliberately, since no subclass fits a store that was never read, and both the CLI and the
  generated runner already catch the base — so it reaches the user as a message rather than a
  traceback. All of this is scoped to `login()`: the other readers run with nobody at the
  keyboard, and there "start fresh" is not theirs to decide. (A keyring blob that will not
  parse is a different path: `_keyring_load` already falls back to the file store with a
  warning, and nothing is quarantined.)

  A store that parses into something *other* than an object — `[]`, `null`, a bare string,
  the other plausible outcome of a hand-edit gone wrong — reached the same dead end one door
  over: it survived the parse and died two lines later inside `data.pop(...)` as a raw
  `TypeError`. Both credential readers now reject a non-object store where they read it,
  with the same `JSONDecodeError` unparseable bytes already raise, so the quarantine covers
  it unchanged and `get_tokens`/`get_client_info` fail with a sentence rather than an
  `AttributeError`. Who *raises* widened; who *quarantines* did not.

- **A wide exception group could still produce an unreadable "one-line" error.**
  `_describe()` capped each leaf but not their number, so an N-leaf `BaseExceptionGroup`
  rendered as N × ~215 characters on a single CLI line. The joined result is now capped too.

### Added

- **`LoginWontHelp`**, exported from `mcpgen` — the base class for every auth failure
  another browser round cannot fix. `PostLoginCheckFailed` and the new
  `TokenRefreshUnavailable` both inherit from it, so batch callers catch one type and abort
  instead of tracking a growing list. `ReauthenticationRequired` deliberately stays outside
  it: there the browser *is* the fix, and folding it in would make one `except` clause
  swallow both answers.

- **`TokenRefreshUnavailable`**, exported from `mcpgen`. Raised when a cached grant was not
  renewed for any reason other than the authorization server naming the credential dead.
  The refresh token is untouched in every one of them, so the browser has nothing to
  replace; the message says which case it was and whether retrying is the move.

### Changed

- **A token endpoint that rejects a dead grant with a bare `400`/`401` and no JSON `error`
  body no longer triggers an automatic browser login.** This is the most visible change
  here, and it is a deliberate trade. RFC 6749 §5.2 requires that body on exactly those two
  statuses, so a bare or HTML one is far more often a proxy, a gateway, or a WAF than the
  authorization server — and the old behaviour opened a browser that met the same block,
  once per item across a batch. Servers that violate §5.2 lose the automatic prompt: they
  now raise `TokenRefreshUnavailable`, whose message names `mcpgen login <server>` as the
  manual route. If you hit this against a real authorization server, that command still
  recovers it in one step.

- **A refresh-time `502` no longer raises `ReauthenticationRequired`.** Consumers following
  `doc/USAGE.md` catch only that type today, so their handler stops firing for this case —
  which is the point, since it opened a browser that could not help. Catching
  `LoginWontHelp` alongside it is the one-line migration; `mcpgen login` and the
  `generate-mcp-runner` OAuth template already do.

## [0.6.0] — 2026-08-24

### Fixed

- **A completed login was thrown away whenever the server failed right after it, turning a
  transient outage into an endless browser re-prompt.** `login()` stashed the existing
  credential, ran the OAuth flow, then smoke-tested the result with `initialize()` +
  `list_tools()` — with the whole block under one `except BaseException` that restored the
  stash. But the SDK's `OAuthClientProvider` saves the exchanged token from inside the auth
  handshake, before `initialize()` returns, so any failure after that point rolled a valid
  new token back to the stale one. A `502` from the resource server was indistinguishable
  from "your login failed", and nothing was ever cached, so the next run re-prompted — one
  reported batch produced 16 completed browser logins, all discarded, and no useful work.
  The restore is now conditional: it fires only when the flow produced no token at all. When
  a token *was* issued it is kept, together with the token endpoint needed to refresh it later,
  and the failure surfaces as the new `PostLoginCheckFailed` rather than the raw transport
  error. A login that fails *before* the token exchange — a cancelled consent screen, a
  callback timeout — still restores the previous credential whole, `client_info` included:
  pairing a freshly registered `client_id` with a refresh token the authorization server
  issued to the *old* client would break the refresh the restore exists to preserve.

- **Servers that publish no OAuth discovery document re-prompted at every token expiry.**
  The token endpoint was cached only when discovery returned metadata, so for those servers
  `login()` stored a token with no way to renew it, and the next pre-flight refresh had no
  choice but to demand a new browser login — every hour, forever. The endpoint now comes from
  the SDK's own resolution, which covers the no-discovery case with its `<origin>/token`
  fallback, so the cached URL is the one that demonstrably just issued the token rather than a
  guess.

### Added

- **`PostLoginCheckFailed`**, exported from `mcpgen`. The counterpart to
  `ReauthenticationRequired`, and deliberately not named for a cause: the token was issued
  and cached, which says nothing about whether the resource server accepted it. A `502` from
  the origin, a post-login `401` over scope or audience, and an MCP-level error from
  `list_tools()` all raise it; what they share is that another browser round cannot fix them.
  Batch callers can now abort on the first failure instead of walking a user through one
  browser prompt per item. `mcpgen login` reports it as a single `[login] error: …` line —
  naming the underlying cause, which anyio otherwise hides behind "unhandled errors in a
  TaskGroup (1 sub-exception)" — and exits `1`. User interrupts (`KeyboardInterrupt`,
  `SystemExit`, cancellation) are never relabelled, even when a task group wraps them.

### Changed

- **Generated OAuth runners now stop on `PostLoginCheckFailed` instead of printing a
  traceback.** The `generate-mcp-runner` skill's HTTP + OAuth template called `ensure_login()`
  bare, so the one place mcpgen writes caller code did not demonstrate the contract it
  documents. It now prints the message and exits `1` — the failure is not something a rerun,
  or another trip through the browser, can clear.

## [0.5.0] — 2026-08-09

### Fixed

- **A caller-supplied credentials path was silently ignored once past login, so tokens were
  written to one file and read from another.** `login()` and `ensure_login()` accepted
  `creds_path` and honoured it, but the call path never did: `_http_session()` built its
  `FileTokenStorage` on the default `~/.mcpgen/credentials.json`, and neither `session()` nor
  `McpBridgeCaller` even accepted the parameter, so there was no way to route a call to the file
  the login had just written. Anyone keeping tokens outside the shared store — a project-local
  credentials file, two identities side by side, a test fixture — logged in successfully and then
  got `ReauthenticationRequired` on the very next call, in a loop, with both files on disk looking
  plausible. `creds_path` now flows through `_http_session()`, `session()`, and
  `McpBridgeCaller`, defaulting to `DEFAULT_CREDS_PATH` when omitted, so existing behaviour is
  unchanged. The stdio, bearer, static-header, and raw-URL transports do not take it: they store
  no credentials at all, and a parameter that silently does nothing is worse than its absence.

### Added

- **`--creds PATH`** on every command that touches stored credentials — `codegen`, `list`,
  `probe`, `call`, `login`, and the management trio `list-creds`, `delete-creds`,
  `migrate-creds` — the CLI surface for the fix above. `mcpgen login` previously dropped the
  path entirely, having no flag to drop it from; it now logs in to exactly the file the
  subsequent calls will read. The management commands were the other half of the same gap: all
  three already took a `credentials_path` internally but exposed no way to set it, so after a
  `--creds` login `list-creds` reported an empty store, `delete-creds` deleted nothing while
  saying so, and `migrate-creds` migrated the wrong file. A flag that exists on half the
  commands that read a file is worse than no flag at all — it makes the other half look broken.

- **`DEFAULT_CREDS_PATH` is exported from `mcpgen`** — consumers that thread a credentials path
  through their own CLI need the default as a value. Re-deriving
  `Path.home() / ".mcpgen" / "credentials.json"` downstream would silently drift the day mcpgen
  changes it.

## [0.4.0] — 2026-08-09

### Added

- **Headless OAuth login — `mcpgen login <server> --headless`** — logging in no longer requires
  a browser on the machine running the command. Instead of opening a browser and binding a local
  callback server, mcpgen prints the authorization URL to stderr, you authorize on any device,
  and paste the resulting callback URL back on stdin. This is the only workable flow inside
  containers, over SSH, and in CI, where `webbrowser.open()` silently does nothing and the
  callback port is unreachable from wherever the browser actually runs. The redirect URI
  registered in headless mode is port-less (`http://localhost/callback`) — nothing ever fetches
  it, and keeping it free of an ephemeral port makes the registered value stable across runs for
  servers that pin `redirect_uris`. Without the flag mcpgen auto-detects: macOS and Windows are
  always treated as interactive, other platforms are headless when neither `DISPLAY` nor
  `WAYLAND_DISPLAY` is set. `MCPGEN_HEADLESS=1`/`0` overrides the detection in either direction
  for environments that guess wrong; an explicit `--headless`/`--no-headless` outranks both.

- **`mcpgen login --callback-timeout SECONDS`** — the 300-second bound on the browser redirect
  is a guess about how long a consent screen plus MFA takes, and guesses about human latency are
  wrong for someone: a hardware-token or approve-on-phone flow can outlast it, while a scripted
  login wants to fail fast. The flag sets that bound per invocation, `0` restores the original
  unbounded wait as an escape hatch, and anything negative or non-numeric is rejected by argparse
  with a usage message rather than surfacing as a traceback from inside asyncio. The same value
  is available in code as `callback_timeout=` on `login()`, `ensure_login()`, and
  `ensure_login_all()`. Headless logins ignore it — the pasted-URL prompt is never bounded.

- **`ensure_login_all(servers)`** — pre-flight refresh-or-login across several servers in one
  call, for pipelines that talk to more than one MCP server and want every token settled before
  work starts. Deliberately sequential: concurrent logins would open several browser tabs at
  once and, in headless mode, race each other for stdin. Servers with a valid cached token stay
  silent, so the steady-state cost is zero.

### Fixed

- **An authorization denial in the browser flow now fails with the same clear error as the
  headless flow.** The local callback server extracted only `code` and `state` and ignored
  `error`/`error_description`, so declining consent resolved the callback to `(None, None)` and
  surfaced later as an opaque downstream failure. Both callback paths now share one parser,
  which raises `ValueError: OAuth authorization failed: <error> — <description>` at the point of
  failure.

- **`mcpgen login` no longer hangs forever when the browser never comes back.** The error-redirect
  fix above only helps when the authorization server actually redirects; real ones often don't —
  cancel the consent screen and the tab simply closes, so no request ever reaches the local
  callback server and the wait never ends. The interactive flow now bounds that wait at 300s
  (`_CALLBACK_TIMEOUT`) and raises `TimeoutError` explaining that the browser never returned,
  with a pointer to `--headless` for pasting the redirect URL by hand. The timeout cancels the
  pending callback future, so the background server task shuts down cleanly, and it unwinds
  through the same restore path as any other login failure — a timed-out attempt leaves a
  previously-working credential intact. Headless logins are deliberately unbounded: a human
  pasting a URL may take arbitrarily long.

## [0.3.0] — 2026-07-14

### Fixed

- **`mcpgen login` — OAuth token exchange failed with `400 invalid_request`** on authorization
  servers that strictly enforce RFC 6749 §2.3 (`"Client must not use multiple authentication
  methods"`). Dynamic client registration omitted `token_endpoint_auth_method`, so the server
  defaulted us to `client_secret_basic` and issued a `client_secret`; the MCP SDK then sent an
  `Authorization: Basic` header *and* `client_id` in the form body — two client authentication
  methods in one request. mcpgen now registers as a public client
  (`token_endpoint_auth_method: "none"`), which is the correct posture for a distributed CLI
  (RFC 8252 §8.4) and is fully secured by the PKCE (S256) the SDK already performs on every
  authorization code grant. No security regression: the discarded `client_secret` was issued
  per-install by dynamic registration and stored in the same `credentials.json` as the refresh
  token, so anyone who could read the secret could already read the token — it never added a
  layer of defense.

- **Discriminated-union codegen mistyped `string` discriminators with numeric-looking
  enum values as `int`.** Discriminator type was inferred solely by sniffing whether
  shape-spec variant keys looked numeric (e.g. `"1"`, `"2"`), so a genuinely
  `type: "string"` discriminator with those enum values got a wrong `int` `Literal`
  in the generated `@overload` stubs. Discriminator type is now resolved from an
  explicit `input_overrides` entry, then the tool's declared `inputSchema` type,
  and only falls back to key-sniffing when neither is available.

- **`mcpgen call`/`probe` crashed on `resource_link` content items** — real MCP SDK
  `ResourceLink.uri` is a pydantic `AnyUrl`, not `str`; `json.dumps` on the summarized
  content raised `TypeError`. The summary now coerces `uri` to `str`.

- **`mcpgen probe` reported inflated payload sizes for non-ASCII content** — size was
  measured as the character count of `json.dumps`'s default `ensure_ascii=True`
  output, which escapes every non-ASCII character to a `\uXXXX` sequence. Size is
  now measured as real UTF-8 byte length.

## [0.2.0] — 2026-06-20

### Added

- **`mcpgen list --schema`** — include raw `inputSchema` JSON per tool in list output.
  Useful for inspecting required params and enum constraints without a separate probe.

- **`mcpgen codegen --embed-schema`** — embed `fn.__schema__ = {<inputSchema>}` on each
  generated function, plus an Args docstring section (per-param description, enum values,
  default). Enables introspection at runtime (`mod.get_issue.__schema__`) and richer IDE
  hover docs.

- **Enum params → `Literal[...]`** (default, no flag) — `py_type()` now maps JSON Schema
  `enum` arrays to `Literal[v1, v2, ...]` instead of bare `str`/`int`. Applies to direct
  enum params and array items (`list[Literal[...]]`). Static analysis and call-site type
  narrowing work without any extra configuration.

## [0.1.0] — initial release

### Added

- **`mcpgen codegen`** — generate typed async Python wrappers from a live MCP server's
  `tools/list`. Every tool becomes an `async def` typed from `inputSchema`; returns `Any`
  by default (shape-spec refines to `TypedDict`).

- **Shape-spec sidecar** (`<server>.shapes.json`) — hand-editable file driving typed
  return models. Fields: `unwrap` (key path), `return_model` (TypedDict name), `fields`,
  `input_overrides`, `overloads`. Intermediate probe parts land in `.parts/` until `merge`.

- **Discriminator detection + overloads** — `mcpgen list` / `probe` emit a stderr advisory
  when a param is shared across ≥2 tools (polymorphic-suspect). Codegen emits one
  `@overload` per discriminator variant (`Literal[<val>]`) plus a union impl.

- **`mcpgen list`** — print all tools on a server as JSON `[{name, description}]`.
  Includes discriminator advisory on stderr.

- **`mcpgen probe`** — live call(s) → response-shape skeleton. Writes per-probe
  intermediates to `.parts/` for later merge.

- **`mcpgen merge`** — consolidate `.parts/` into `<server>.shapes.json`; emits a
  gitignored `verify.json` sidecar with pre-scrub `probed_args`.

- **`mcpgen call`** — single live call, raw payload written to disk; useful for
  bootstrapping ids and inspecting raw output.

- **`mcpgen discover`** — enumerate MCP servers configured in installed agent hosts
  (reads Claude Code CLI / `~/.claude.json`).

- **`mcpgen login`** — browser OAuth login; tokens stored at
  `~/.mcpgen/credentials.json` (chmod 0600) or OS keychain via `--cred-backend keyring`.

- **`mcpgen migrate-creds`** — move stored OAuth tokens between `file` / `keyring`
  backends.

- **`mcpgen list-creds` / `delete-creds`** — inspect and remove stored credentials.

- **Transport flags** — `--stdio` (launch command), `--url` (HTTP), `--bearer` (PAT),
  `--config` (config-file server resolution); shared across `codegen`/`list`/`probe`/`call`/`login`.

- **`generate-mcp-wrappers` plugin skill** — agent skill that drives the full
  probe → shape-spec → codegen workflow interactively.

- **`generate-mcp-runner` plugin skill** — agent skill for building typed runner
  scripts on top of generated wrappers.
