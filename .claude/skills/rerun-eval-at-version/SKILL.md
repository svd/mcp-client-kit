---
name: rerun-eval-at-version
description: Use when re-running the generate-mcp-wrappers eval against a specific mcp-client-kit release — pins the engine, registers the matching skill worktree through the claude plugin CLI, refuses to continue until both agree, then stops and hands the sanity run back to the user.
disable-model-invocation: true
---

# Re-running the eval at a pinned version

## Overview

Core principle: **an eval result means nothing without the version that produced it.**
Two things move independently — the **engine** (`mcp-client-kit` distribution in `.venv`,
providing `mcpgen`) and the **skill** (`generate-mcp-wrappers` SKILL.md, loaded from the
`mcp-client-kit` plugin marketplace directory). Pin both to the same release, prove they
agree, then run.

## When to use

Before `/run-eval` when the target version differs from what is installed — a new
`mcp-client-kit` release, or reproducing an old run. For a repeat run at the version
already installed, skip this and call `/run-eval` directly.

Invoke as `/rerun-eval-at-version 0.7.0` or `/rerun-eval-at-version latest`.

This skill moves the repo from local-dev mode (editable `[tool.uv.sources]` path to
`../mcp-client-kit`, plugin served from that same checkout) to pinned-release mode. Going
the other way is the reverse of Steps 2 and 4: restore the `[tool.uv.sources]` entry, point
the plugin marketplace back at `../mcp-client-kit`, and drop the release worktree.

## The 8-step workflow

Steps 1, 3, 4 and 7 are **hard gates** — a failure there stops the run. Do not work around
a gate; report it and wait.

**Step 1 — Resolve the target**

`latest` resolves against PyPI:

```bash
curl -s https://pypi.org/pypi/mcp-client-kit/json \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
```

Then confirm the matching tag exists in the source repo:

```bash
git -C ../mcp-client-kit tag --list "v<X>"
```

**GATE:** no tag `v<X>` → stop. A PyPI release without a tag means the skill cannot be
pinned to the same code as the engine, and the run would mix versions.

**Step 2 — Pin the engine**

Set the exact pin in `pyproject.toml` (`==`, never `>=` — the run must be reproducible) and
drop the `[tool.uv.sources]` entry if the repo is in local-dev mode, otherwise the editable
checkout wins over the pin:

```toml
dependencies = ["mcp-client-kit==<X>"]
```

```bash
uv lock --upgrade-package mcp-client-kit && uv sync
```

**Step 3 — Prove the engine**

```bash
uv run python -c "from eval_harness.versions import engine_version; print(engine_version())"
```

**GATE:** output ≠ `<X>` → stop and report. Everything downstream is mislabelled otherwise.

**Step 4 — Check out and register the skill**

The plugin is served from a worktree of the source repo, so the main checkout stays on
its working branch:

```bash
git -C ../mcp-client-kit worktree add ../mcp-client-kit-v<X> v<X>
```

Already exists → fine, move on. Then detect what is actually registered:

```bash
uv run python -c "from eval_harness.versions import runtime_versions; print(runtime_versions())"
```

`skill_ref` == `v<X>` already → nothing to do, go to Step 5. Otherwise register the
worktree through the `claude plugin` CLI. The agent cannot run slash commands, but the CLI
covers the same ground and does not need the user in the loop.

The marketplace name `mcp-client-kit` is unique across scopes, so a declaration pointing at
another checkout must go before the new one can land. Find where it lives — `user` and
`project` scopes write `extraKnownMarketplaces` into a settings file, `local` scope does
not, so check the plugin registry too:

```bash
python3 -c "
import json, os
for f in ('~/.claude/settings.json', '.claude/settings.json', '.claude/settings.local.json'):
    path = os.path.expanduser(f)
    if os.path.exists(path):
        d = json.load(open(path)).get('extraKnownMarketplaces', {})
        if 'mcp-client-kit' in d: print(f, '->', d['mcp-client-kit'])
reg = os.path.expanduser('~/.claude/plugins/known_marketplaces.json')
print('registry ->', json.load(open(reg)).get('mcp-client-kit'))"
```

The registry line is where it currently points; the settings hit, if any, names the scope.
Removing a **user**-scope declaration edits global settings and affects every other project
on the machine — back it up, say so plainly, and get an explicit yes before removing that
scope. Project- and local-scope declarations are repo-local: remove without ceremony.
Omitting `--scope` removes from every scope, which is the right call when no settings file
claims it.

```bash
cp ~/.claude/settings.json "$SCRATCHPAD/settings.json.bak"
claude plugin marketplace remove mcp-client-kit --scope <scope-found-above>
claude plugin marketplace add /absolute/path/to/mcp-client-kit-v<X> --scope local
claude plugin install mcp-client-kit@mcp-client-kit -s local -y
```

Local-scope installs record themselves in `~/.claude/plugins/installed_plugins.json`
(keyed by scope and project path) and copy the plugin into
`~/.claude/plugins/cache/`, not into the repo — nothing to commit, nothing to clean up in
`.claude/`.

Ask the user which scope to install at if they have not said; `local` is the default
because it stays in the plugin registry, while `project` writes `extraKnownMarketplaces`
into the tracked `.claude/settings.json` and would commit an absolute machine path. Use the
**same** scope for `marketplace add` and `install`, or the install cannot resolve the
marketplace. `-y` is required because stdout is not a TTY under the Bash tool.

Then re-check:

```bash
uv run python -c "from eval_harness.versions import runtime_versions; print(runtime_versions())"
```

**GATE:** `skill_ref` ≠ `v<X>` → stop and report. Do not start a sweep against a skill that
does not match the engine.

Record for the hand-off what the reverse looks like: `claude plugin marketplace add
/absolute/path/to/mcp-client-kit` (no `--scope`, defaults to user) restores the dev
checkout, and the backup file is the fallback.

**Step 5 — Baseline**

```bash
git status --porcelain
```

Only `pyproject.toml` and `uv.lock` should be dirty — that is Step 2's pin. Commit them, so
the baseline commit *is* the version switch:

```bash
git add pyproject.toml uv.lock && git commit -m "chore(deps): pin mcp-client-kit at <X>"
```

Anything else dirty → stop; the previous run's artifacts must be committed first, since the
new run overwrites `eval/` in place. Tell the user:

- the previous run is the current `HEAD` — `git diff` after the run is the version-to-version diff;
- rollback is `git checkout eval/`;
- untracked-in-git files (`result.json`, `session-analyzer.md`, `_synthesis.md`) are replaced,
  and all of them are regenerated by the pipeline.

**Step 6 — Stop and hand off**

Environment prep ends here. **Stop.** Do not run `/run-eval` — a slash command is the
user's to invoke, and a plugin swapped in mid-session is not necessarily the plugin the
next subagent loads.

Report a table of `engine`, `skill_ref`, `skill_path`, install scope, and the baseline
commit sha, then tell the user:

- **restart the session before the sanity run.** `claude plugin install` writes the plugin
  registry, but plugin content is loaded at session start — `runtime_versions()` reads that
  registry and will already look correct while `/run-eval` subagents still load the old
  SKILL.md. There is no verified in-session reload command; a fresh session is the only
  path this skill trusts. (`claude plugin update --help` says the same: "restart required
  to apply".)
- any global settings change made in Step 4, where the backup is, and the exact command
  that reverses it;
- rollback for artifacts is `git checkout eval/`, and `git diff <baseline-sha>` after the
  run is the version-to-version diff;
- next command, verbatim: `/run-eval time` (Step 7).

**Step 7 — Sanity run on one server**

```
/run-eval time
```

Then confirm the stamp landed:

```bash
python3 -c "import json;print(json.load(open('eval/time/result.json'))['versions'])"
```

**GATE:** `engine` ≠ `<X>` or `skill_ref` ≠ `v<X>` → stop. Cheap single-server check before
committing to a full sweep.

**Step 8 — Full sweep**

```
/run-eval all
```

then `/triaging-eval-outputs`, then:

```bash
uv run eval-kit report --with-narrative
```

Confirm the report header reads `Engine: mcp-client-kit <X> · skill ref: v<X>`. A
`⚠️ mixed engine versions` line means some servers were not re-run — re-run the named ones
before drawing conclusions.

## Version stamping

`eval_harness/versions.py` is the single source of truth:

| Value | Source |
|---|---|
| `engine` | `importlib.metadata.version("mcp-client-kit")` |
| `skill_ref` | `git describe --tags --always --dirty` in the plugin directory |
| `skill_path` | plugin directory from `~/.claude/plugins/known_marketplaces.json` |

`verify.py` writes all three into every `result.json` under `"versions"`; `report.py`
renders them in the report header. Detection never raises — undetectable values are `None`
and surface as `unknown`. Override the ref with `EVAL_SKILL_REF` when the skill is served
from something that is not a git checkout.

## Common mistakes

- **Engine pinned, skill forgotten:** `uv sync` alone leaves the plugin on the old ref. The
  run then measures a new engine against an old SKILL.md. That is what the Step 4 gate is for.
- **`>=` instead of `==`:** a later resolve silently upgrades the engine and the two runs
  stop being comparable.
- **Detaching the main checkout:** `git checkout v<X>` in `../mcp-client-kit` disrupts the
  working branch. Use a worktree.
- **Sanity run without restarting:** `runtime_versions()` reads the plugin registry, so the
  gate goes green the moment the CLI writes it — while `/run-eval` subagents still load the old
  SKILL.md from the previous session's plugin load. The result stamps the new version onto
  old-skill behaviour, which is worse than a mismatch that stops the run.
- **Mismatched scopes:** `marketplace add --scope local` plus `install -s user` leaves the
  install unable to resolve the marketplace. Same scope on both.
- **Skipping the single-server sanity run:** a full sweep is expensive; a version mismatch
  found at server 13 wastes all of it.
- **Reading a `⚠️ mixed` report as a result:** it is a partial re-run, not a finding.
