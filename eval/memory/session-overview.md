# Session Overview: memory MCP Server

## Run Metadata

- **Executed:** 2026-06-19T22:43:22Z
- **Duration:** 2m 5s (wall-clock around the generate-mcp-wrappers skill run, including subagent steps)

## Server Overview

The `memory` server is the `@modelcontextprotocol/server-memory` knowledge graph server, launched via `npx -y @modelcontextprotocol/server-memory` over stdio. It exposes **9 tools** for managing an in-memory knowledge graph of entities and relations.

## Tool Selection

Of the 9 tools, **6 are mutating** and were skipped per the subagent fallback policy (probe only non-mutating tools):

- `create_entities` — creates entities [MUTATING]
- `create_relations` — creates relations [MUTATING]
- `add_observations` — adds observations to existing entities [MUTATING]
- `delete_entities` — deletes entities [MUTATING]
- `delete_observations` — deletes specific observations [MUTATING]
- `delete_relations` — deletes relations [MUTATING]

The remaining **3 read-only tools were probed**:

- `read_graph` — reads the entire knowledge graph (no args)
- `search_nodes` — searches nodes by query string
- `open_nodes` — retrieves specific nodes by name array

No discriminator candidates were flagged by `mcpgen list` — all tools accept distinct arg shapes and share no polymorphic parameter.

## Probe Findings

The knowledge graph store was **not empty** at probe time — it contained 3 entities (`AlicePerson`, `Alice`, `ProjectX`) and 1 relation (`Alice works_on ProjectX`). This allowed all three read-only tools to return meaningful non-empty payloads.

All three tools returned the **same top-level response shape**: `{entities: [...], relations: [...]}`. No vendor envelope or intermediate nesting was present — the meaningful record is directly at the root.

- `read_graph` — returned `{entities: [3 items], relations: [1 item]}`. Probed with `{}` (no args required). Shape confirmed in first probe.
- `search_nodes` — queried with `"Alice"`, returned `{entities: [2 matching items], relations: []}` (empty relations list for that search). The `relations` key was still present but contained `<empty>` placeholder, confirming the field always exists but may be empty.
- `open_nodes` — called with `["Alice", "ProjectX"]`, returned `{entities: [2 items], relations: [1 item linking them]}`.

## Shape Decisions

All three read-only tools share an identical response shape, so a single `TypedDict` named **`KnowledgeGraph`** was minted with two top-level list fields:

- `entities: list` — list of entity dicts (`name`, `entityType`, `observations`)
- `relations: list` — list of relation dicts (`from`, `to`, `relationType`)

The inner element types were kept as `list` (not further typed to a nested `TypedDict`) because modeling one level of depth is sufficient to be useful without over-constraining the shape from a single probe. The entity observation arrays and relation structs are stable and simple, but the skill's guidance is to avoid deep-modelling from a single sample.

No `unwrap` path was needed — the response is already the `KnowledgeGraph` dict at the top level, with no vendor envelope. `return_container` was omitted since each tool returns a single graph object, not a list of objects.

No PII was present in `probed_args` — entity names like `"Alice"` and `"ProjectX"` are synthetic demo data in this knowledge graph server's default memory store. No scrubbing was required.

## Verification

The regenerated `memory.py` parsed cleanly (`ast.parse` returned no errors). All three shaped tool signatures read `-> KnowledgeGraph` confirming the shape was applied correctly. The `KnowledgeGraph` TypedDict is defined with `total=False` and two `list` fields. The six mutating tools retain `-> Any` as expected.
