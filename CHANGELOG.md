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
