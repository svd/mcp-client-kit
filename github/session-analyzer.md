# Session Report: generate-mcp-wrappers for GitHub Copilot MCP Server

**Session ID:** `abc1f701-a304-4f37-b86b-1a0e77fabfff`
**Date:** 2026-06-16 (file mtime; session entries have no embedded timestamps)
**Model:** claude-sonnet-4-6
**Skill invoked:** `mcp-client-kit:generate-mcp-wrappers`
**User request:** "for github server, store results in test-github folder, use GITHUB_PAT env variable"

---

## 1. Tool Calls Summary

### Main Session (39 total)

| # | Tool | Count | Attribution |
|---|------|-------|-------------|
| 1 | Bash | 24 | Mixed — see details below |
| 2 | Agent | 6 | Skill-driven (recon + 5 batch probe subagents) |
| 3 | Edit | 4 | Skill-driven (shapes.json dedup cleanup) |
| 4 | Read | 3 | Skill-driven (servers.toml, generated module, shapes.json) |
| 5 | AskUserQuestion | 1 | Skill-driven (Step 2c interactive gate) |
| 6 | Write | 1 | Skill-driven (shapes.json manual edit before merge) |

**Bash breakdown (24 calls):**

| Bash call purpose | Attribution |
|---|---|
| seq=1: Explore project layout (`ls`) | LLM autonomous |
| seq=2,4: `mcp-kit list github` without `--url`/`--bearer` (2× failure) | LLM autonomous (wrong CLI invocation) |
| seq=5,6: `mcp-kit --help`, `mcp-kit list --help` | LLM autonomous (CLI discovery after error) |
| seq=7: Verify PAT env var then retry `mcp-kit list` with `--config` | LLM autonomous (error recovery) |
| seq=8,9: `mcp-kit list github --url … --bearer` (two formats) | Skill-driven (Step 2a) |
| seq=10: `mcp-kit codegen --help` | LLM autonomous (CLI discovery) |
| seq=11: `mcp-kit codegen github --url … --out test-github/github.py` | Skill-driven (Step 1) |
| seq=13–18: Inspect generated stubs (grep, head) and `mcp-kit list` for discriminator analysis | Skill-driven (Step 2 discriminator detect) |
| seq=25: `ls .parts/` to verify part files written | Skill-driven (Step 3b pre-merge check) |
| seq=26: `mcp-kit merge github --out …github.shapes.json` | Skill-driven (Step 3b merge barrier) |
| seq=29,37,39: `mcp-kit codegen … --shapes` (3× regeneration iterations) | Skill-driven (Step 5) |
| seq=30–32: `ast.parse` verify + `grep TypedDict classes` + duplicate class check | Skill-driven (Step 6 verify) |

### Subagent Sessions (6 total)

| Agent | Role | Tool calls | Errors | Key tools |
|---|---|---|---|---|
| SA-0 (recon) | Discover sample IDs for probing — `get_me`, `search_repositories`, `get_teams` | 8 | 0 | Bash (4), Read (4) |
| SA-1 (batch 4) | Probe `issue_read`, `pull_request_read`, `list_issues`, `list_pull_requests`, `list_issue_fields`, `list_issue_types` | 12 | 0 | Bash (6), Read (6) |
| SA-2 (batch 1) | Probe `get_me`, `search_*`, `get_teams` | 29 | 4 | Bash (20), Read (9) |
| SA-3 (recon/verify overlap) | `mcp-kit call` recon for IDs + reading probe outputs | 16 | 0 | Bash (13), Read (3) |
| SA-4 (batch 5) | Probe `get_commit`, `get_file_contents`, `get_label`, `get_team_members`, `run_secret_scanning` | 35 | 9 | Bash (27), Read (8) |
| SA-5 (batch 3) | Probe `list_branches`, `list_commits`, `list_releases`, `list_tags`, `list_repository_collaborators` | 16 | 0 | Bash (8), Read (8) |

Note: SA-2 and SA-4 had the most errors, driven by invalid CLI flags (`--schema`, `--json`, `--verbose`) and probe failures for `run_secret_scanning` (wrong owner typo: `anthropics` vs `anthropic`).

---

## 2. Skill vs. LLM Attribution

| Source | Tool calls | % |
|--------|-----------|---|
| `mcp-client-kit:generate-mcp-wrappers` (skill-driven) | 96 | ~86% |
| LLM autonomous | 16 | ~14% |

**Skill-driven actions** (explicitly prescribed by SKILL.md):
- `mcp-kit list` to enumerate tools with discriminator detection (Step 2a)
- `AskUserQuestion` for the "Probe all / Confirm in batches / I'll specify" gate (Step 2c)
- Dispatch of a recon subagent to collect sample IDs before probing (Step 3 / Execution model)
- Dispatch of 5 parallel batch subagents for probe + draft (Step 3 / batching rule)
- `mcp-kit merge` as the deterministic barrier after all batch agents finish (Step 3b)
- `mcp-kit codegen --shapes` regeneration (Step 5)
- AST parse + `grep TypedDict` verification (Step 6)
- `Edit` to tune shapes.json dedup before final regeneration (Step 4 consistency)

**LLM autonomous actions** (not prescribed — 14%):
- `ls` to explore project layout before starting
- Three failed `mcp-kit list github` calls without `--url`/`--bearer` (CLI discovery loop)
- `mcp-kit --help` and `mcp-kit list --help` after the initial failures
- `mcp-kit codegen --help` to discover `--shapes` flag syntax
- Two subagent errors from inventing non-existent flags (`--schema`, `--json`, `--verbose`, `--full`) on `mcp-kit list` and `mcp-kit probe`

Attribution is inferred from SKILL.md cross-referenced with the parsed tool call sequence. No external agent `.md` files were loaded.

---

## 3. Errors and Recovery

### Error 1 — Bash (seq=2): `mcp-kit list github` fails without `--url`

**What happened:** The skill was invoked with `use GITHUB_PAT env variable` but the LLM first tried `mcp-kit list github` without specifying the URL or bearer token, relying on auto-discovery from servers.toml that requires a different config path format.

**Error output:**
```
+ Exception Group Traceback (most recent call last):
  |   File "/Users/Sviataslau_Svirydau/src/mcp-client-kit/.venv/bin/mcp-kit", line 10, in <module>
  |     sys.exit(main())
  |              ~~~~
```

**How the LLM recovered:** Read `servers.toml` (seq=3) to understand the server config, then retried with `--config` flag (seq=4) — which also failed. Then ran `mcp-kit --help` and `mcp-kit list --help` (seq=5–6) to discover the `--url`/`--bearer` flags, and succeeded on seq=7–8.

**Root cause and fix options:**
- Option A: The skill prompt should prescribe `--url` and `--bearer` flags explicitly when the server entry uses `transport = "http"`. Currently SKILL.md shows `mcp-kit list <server>` without the connection flags, which only works for stdio transports.
- Option B: The servers.toml entry for `github` could embed the PAT credential reference so `mcp-kit` resolves it automatically from the config file, removing the need for CLI flags.

---

### Error 2 — Bash (seq=4): `mcp-kit list github --config` also fails

**What happened:** After reading servers.toml, the LLM tried `mcp-kit list github --config /path/servers.toml` but the `--config` flag either doesn't exist or requires a different invocation format.

**Error output:**
```
+ Exception Group Traceback (most recent call last):
  File "/Users/Sviataslau_Svirydau/src/mcp-client-kit/.venv/bin/mcp-kit", line 10, in <module>
```

**How the LLM recovered:** Ran `mcp-kit --help` (seq=5) and `mcp-kit list --help` (seq=6) to discover the correct flags; then succeeded using `--url` and `--bearer`.

**Root cause and fix options:**
- Option A: SKILL.md should include the specific CLI invocation for HTTP servers vs stdio servers, so the LLM skips the help-discovery loop entirely.
- Option B: Surface the `--config` flag's exact format in the servers.toml comment so the LLM can infer the correct invocation.

---

### Error 3 — Bash (SA-2, seq=9): `mcp-kit probe --schema` does not exist

**What happened:** SA-2 (batch 1 probe agent) tried to retrieve the input schema for `search_users` using a `--schema` flag that does not exist on `mcp-kit probe`.

**Error output:**
```
Exit code 2
mcp-kit: error: unrecognized arguments: --schema
```

**How the LLM recovered:** The agent abandoned the schema inspection attempt and instead read the already-generated `github.py` stub to infer the input schema from the function signature.

**Root cause and fix options:**
- Option A: The batch agent prompt should list only the valid `mcp-kit probe` flags. The agent invented `--schema` from general intuition. Add explicit negative guidance ("there is no `--schema` flag").
- Option B: Add a `mcp-kit probe --schema <tool>` command to the CLI if this is a common need.

---

### Error 4 — Bash (SA-2, seq=16,17): `mcp-kit list --json` and `--full` do not exist

**What happened:** The agent tried to get raw JSON output from `mcp-kit list` using `--json` and `--full` flags, which are also not valid.

**Error output:**
```
ERR Expecting value: line 1 column 1 (char 0)
mcp-kit: error: unrecognized arguments: --json
```

**How the LLM recovered:** The agent gave up on the JSON extraction approach and instead used the text output from `mcp-kit list` directly to infer the schema.

**Root cause and fix options:**
- Option A: SKILL.md should document that `mcp-kit list` has no JSON output mode and describe how to access raw tool schemas if needed.
- Option B: Add `--json` output mode to `mcp-kit list` to make schema inspection programmatic.

---

### Error 5 — Bash (SA-4, seq=4): Permission error — `claude-sonnet-4-6 temporarily unavailable`

**What happened:** SA-4's first attempt to probe `get_team_members` was blocked by the auto-mode safety check: `claude-sonnet-4-6 is temporarily unavailable, so auto mode cannot determine the safety of Bash right now`.

**Error output:**
```
claude-sonnet-4-6 is temporarily unavailable, so auto mode cannot determine the safety of Bash right now. Wait briefly and then try this action again.
```

**How the LLM recovered:** The agent retried the probe after the permission block resolved (seq=14 shows a retry, though it failed with `--verbose` flag this time). Eventually the agent probed `get_file_contents` successfully and inferred `get_team_members` shape from the list output.

**Root cause and fix options:**
- Option A: This is a transient infrastructure issue (model unavailable for permission check). No action needed, but adding retry logic or a brief wait before retry would help.
- Option B: Pre-approve `mcp-kit probe` commands in `.claude/settings.json` so auto-mode permission checks are bypassed for known-safe CLI tools.

---

### Error 6 — Bash (SA-4, seq=5): `run_secret_scanning` probe fails — wrong repo owner

**What happened:** The agent used `owner: "anthropics"` (wrong) instead of `owner: "anthropic"` when probing `run_secret_scanning`.

**Error output:**
```
Exit code 1
[probe] github.run_secret_scanning (1 probe(s)) …
[probe]   [1/1] args={'owner': 'anthropics', 'repo': 'anthropic-sdk-python', 'files': []}
[probe] wrote part …
```

**How the LLM recovered:** The agent noted the probe wrote a part file (so a result was captured, likely partial/error shape), and retried with corrected args later (seq=34 shows a second attempt with a non-empty files list). The `run_secret_scanning` tool was ultimately classified as `-> Any` (mode A) because the response was a string.

**Root cause and fix options:**
- Option A: The recon agent output should include a validated `owner` string that batch agents use verbatim. Here the recon agent reported `"owner": "anthropics"` with the typo and all batch agents inherited it.
- Option B: The batch agent prompt should include a note to validate the owner against the `get_me` response which contains the authenticated user's org membership.

---

### Errors 7–13 — Bash (SA-4, seq=14–22): Multiple invalid CLI flags on probe and call

**What happened:** SA-4 made 7 additional failed invocations using `--verbose` on `mcp-kit probe`, `--out` on `mcp-kit call` (without `--args`), and `--full`/`--json` on `mcp-kit list`.

**Error pattern:**
```
mcp-kit: error: unrecognized arguments: --verbose
usage: mcp-kit call [-h] [--args JSON] --out FILE [--stdio CMD] …
```

**How the LLM recovered:** The agent eventually fell back to basic `mcp-kit probe` calls with only the supported flags, and successfully completed all assigned probes.

**Root cause and fix options:**
- Option A: The batch agent prompt should contain a minimal CLI cheat-sheet for the 3 commands it uses (`probe`, `call`, `list`) with only the flags that actually exist. The agent invented flags by analogy.
- Option B: `mcp-kit` could emit a friendlier "did you mean?" message for common misused flags.

---

## 4. Token Usage and Cost

| Metric | Main session | Subagents (×6) | Total |
|--------|-------------|----------------|-------|
| Input tokens | 94 | 384 | 478 |
| Output tokens | 178,955 | 25,240 | 204,195 |
| Cache writes | 307,289 | 240,145 | 547,434 |
| Cache reads | 5,142,714 | 2,582,785 | 7,725,499 |
| **Estimated cost** | | | **$7.4349** |

The main session dominates output tokens (178K vs 25K combined subagents) because the skill context (60+ skills loaded into CLAUDE.md) is reconstructed every turn, driving massive cache reads. Cache reads account for 97.4% of all tokens processed, which is expected given the large static context.

*Pricing tier: claude-sonnet-4-6 (sonnet rates). All 7 sessions (main + 6 subagents) ran the same model.*

### Cost by model

| Model | Sessions | Input | Output | Cache write | Cache read | Cost |
|-------|----------|-------|--------|-------------|------------|------|
| claude-sonnet-4-6 | 7 | 478 | 204,195 | 547,434 | 7,725,499 | $7.4349 |

---

## 5. Optimization Recommendations

1. **Teach the skill the HTTP server CLI pattern up front.** The LLM spent 6 Bash calls (seq=2–10) discovering that `mcp-kit list` for HTTP servers requires `--url` and `--bearer`. SKILL.md should add a preamble: "If `transport = "http"`, pass `--url <launch> --bearer $<ENV_VAR>` to all mcp-kit commands." This would eliminate the entire CLI-discovery loop and save approximately 3 round-trips.

2. **Add a CLI cheat-sheet to every batch agent prompt.** SA-2 and SA-4 invented 8 non-existent flags (`--schema`, `--json`, `--full`, `--verbose`, missing `--out` on `mcp-kit call`). A single-table "valid flags for this run" block in the agent prompt — listing only what the agent is allowed to call — would prevent all 13 flag-hallucination errors. This also saves recovery turns which drive subagent cache reads.

3. **Validate the recon catalog before dispatch.** The `owner: "anthropics"` typo in the recon output was inherited by SA-4 and caused at least 2 probe failures plus multiple retry iterations. The main thread should do a quick sanity check on the recon catalog (e.g. `get_me` response shows the authenticated user's orgs) before injecting the IDs into batch agent prompts.

4. **Pre-approve `mcp-kit probe/call/merge/list/codegen` in `.claude/settings.json`.** The auto-mode permission block (SA-4, seq=4) halted a probe mid-batch. Since these commands are read-only relative to the server (probing makes a live read call), adding an allow-rule for `mcp-kit` eliminates the permission gate entirely and prevents transient-availability stalls.

5. **The large skills context (60+ skills) inflates every turn's cache read.** The 7.7M cache-read tokens are dominated by the static CLAUDE.md context being re-read each turn. Consider structuring the eval runner to invoke the skill in a leaner context (project-only settings, no global plugin manifests) to cut per-turn overhead by an estimated 40–60%.
