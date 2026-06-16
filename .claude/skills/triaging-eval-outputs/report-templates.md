# Report templates — triaging-eval-outputs

Use these skeletons for Step 5 of the triaging workflow. Fill in discovered items; delete empty buckets.

---

## Template A: `doc/FIXES-mcp-client-kit.md`

```markdown
# Fix report — mcp-client-kit (`generate-mcp-wrappers` skill + `mcp-kit` CLI)

Generated from: eval of <N> MCP servers, <DATE>.
Scope: confirmed code bugs + recurring friction (2+ servers). Excludes one-off LLM lapses.

This document is **self-contained** — the receiving agent does not have access to the eval repo.
Each item includes verbatim error output and affected server names.

---

## P0 — Confirmed CLI bugs

### 1. <Title>

**Affected:** `<server1>`, `<server2>`

**Symptom:** <1-2 sentence description of what breaks and when.>

```
<verbatim error output — exact text from session-overview>
```

**Root cause:** <mechanistic explanation.>

**Fix:** <concrete code change or doc addition. No vague "improve X".>

---

## P1 — SKILL.md documentation / guidance gaps

### <N>. <Title>

**Affected:** <servers, count> (e.g. "9 servers"; list names if ≤5)

**Symptom:** <what agents do wrong, with verbatim flag/error if applicable.>

**Fix:** <exact text / example to add to SKILL.md, or CLI behavior to change.>

---

## P2 — Low priority / future consideration

### <N>. <Title>

**Affected:** <servers>

**Observation:** <what was noticed; no urgent fix required.>

**Suggestion:** <optional improvement direction.>
```

---

## Template B: `doc/FIXES-eval-kit.md`

```markdown
# Fix report — eval-kit harness

Generated from: eval of <N> MCP servers, <DATE>.
Scope: confirmed code bugs + recurring friction (2+ servers). Excludes one-off LLM lapses.

Files referenced: `eval_harness/runner_gen.py`, `eval_harness/cli.py`,
`agents/server-eval-agent.md`, `workflows/run-eval.workflow.js`, `servers/servers.toml`.

---

## P0 — Confirmed code bugs

### 1. <Title>

**File:** `<path/to/file.py>:<line>`
**Affected:** `<server1>`, `<server2>`

**Symptom:** <what the user/agent sees.>

```
<verbatim error or wrong output — exact text>
```

**Root cause:** <what the code does wrong, citing the line.>

**Fix:** <minimal, precise code change. Include before/after snippet when the change is non-obvious.>

---

## P1 — Harness / process gaps

### <N>. <Title>

**File:** `<path>` (affected section: lines <start>–<end>)
**Affected:** <servers, count>

**Symptom:** <what goes wrong, how many extra tool calls / what fails.>

**Fix:** <what to add/change; quote the exact text to add to a prompt file, or the arg to add to a CLI command.>

---

## P2 — Low priority / note-only

### <N>. <Title>

**Affected:** <servers>

**Symptom:** <brief, specific.>

**Fix:** <one-line note or doc addition.>
```

---

## Per-item shape reference

Every item (in both reports) must satisfy:

| Field | Required | Notes |
|---|---|---|
| Title | yes | Verb-first, specific: "run.py emits hyphenated calls" not "run.py bug" |
| Affected servers | yes | At least 1 named server |
| Symptom | yes | Concrete observable behavior |
| Verbatim error string | P0 only | Exact output; if long, quote the key line |
| `file:line` | P0 eval-kit only | Verified against source before writing |
| Root cause | P0 | Mechanistic, not "the LLM guessed wrong" |
| Fix | all | Actionable, not "improve X" |

**mcp-client-kit report extra constraint:** no eval-repo-only paths (e.g. no `/Users/Sviataslau_Svirydau/src/mcp-client-kit-eval/...`). All paths must be relative to the mcp-client-kit repo or be generic.
