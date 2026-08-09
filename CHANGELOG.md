# Changelog

## [Unreleased] — 0.4.0

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
