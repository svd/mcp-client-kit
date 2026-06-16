# Idea 2: Claude Code skill that generates typed Python wrappers for any MCP server

## What it would do

Given an MCP server (URL or local command), the skill:

1. Connects via the extracted client library, calls `tools/list`.
2. For each tool, reads `name`, `description`, `inputSchema` (JSON Schema).
3. Generates a module like `acme.py`: one `async def` per tool, typed
   arguments derived from the schema, docstring from the description,
   `parse_tool_result` unwrapping.
4. Optionally probes one real call per tool (with user-supplied sample args)
   to record the actual response shape — the key lesson: schemas describe
   *inputs* well, but response shapes need empirical validation.
5. Writes a `servers.toml` entry (URL, verify tool, auth mode) so the
   generated module is runnable immediately.

## Why a skill and not a pure codegen CLI

Two-phase reality:

- **Deterministic part** (tools/list → function stubs) could be a plain
  `mcp-kit codegen` CLI command — no LLM needed. This should live in the
  library, not the skill.
- **Judgment part** (which fields to project, how to unwrap vendor envelopes,
  which tools matter for the user's pipeline, sample-call validation) needs an
  LLM in the loop. That's the skill: it drives the CLI, probes live responses,
  and edits the generated code to match observed shapes.

Recommended split: CLI generates 80% mechanically; skill does the empirical
validation pass and trims/curates. This mirrors how the origin project evolved
(projections were validated one by one against live responses).

## Token-economics argument (why colleagues should care)

The pattern this enables — confirmed by the landscape research (see
LANDSCAPE.md): "code execution with MCP" — LLM writes/uses code that calls
tools, instead of tool schemas + tool results flowing through the model
context every time.

The origin project as case study: stages 1–4 moved from LLM tool-calls to
Python, eliminating per-record JSON payloads (100–500 KB each) from model
context entirely.

## Locked architecture: the client seam (2026-06-14)

Generated wrappers depend on an **injected client Protocol**, never a concrete
import. This keeps generated modules reusable for colleagues and lets the auth
backend swap without regenerating:

```python
from typing import Any, Protocol

class McpCaller(Protocol):
    async def call(self, server: str, tool: str, arguments: dict) -> Any: ...
```

Each generated `async def` takes the caller as its first argument and forwards to
`caller.call(SERVER, "<tool>", {...})`. Behind the seam today sits `mcp_client_kit/_bridge.py` over the official `mcp`
SDK (`ClientSession` + `streamablehttp_client`). FastMCP was evaluated and
rejected as the backend. Wrappers don't change. See VERDICT.md §Correction.

## Risks specific to the skill

- **Schema drift**: generated wrappers go stale when the server changes.
  Mitigation: `mcp-kit codegen --check` mode that re-lists tools and diffs
  against generated code; CI-able.
- **Response-shape assumptions**: generation from inputSchema alone produces
  wrappers that lie about outputs. Mitigation: validation pass is mandatory in
  the skill procedure, optional in CLI.
- **Auth variance**: corporate servers (OAuth PKCE) vs local stdio servers vs
  API-key headers. Library must support all three before the skill can claim
  "any MCP server".
