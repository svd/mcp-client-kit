# Changelog

## [Unreleased] — 0.2.0

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
