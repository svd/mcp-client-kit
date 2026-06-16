# Distribution plan: mcp-client-kit

**Decision:** public GitHub repo. Ships **two artifacts from one repo (monorepo):**

| Artifact | What | Channel | Consumer command |
|---|---|---|---|
| **engine** — `mcp_client_kit` | the codegen CLI + OAuth bridge (Python package) | **PyPI** | `uvx mcp-client-kit …` / `uv add` |
| **plugin** — `mcp-client-kit` | the judgment layer (Claude Code plugin; skill `generate-mcp-wrappers`) | **marketplace** (git repo) | `/plugin marketplace add …` |

(Plugin name = `mcp-client-kit`, matching the package and repo. The skill *inside*
the plugin is `generate-mcp-wrappers` — filesystem `skills/generate-mcp-wrappers/`.)

They are one product with one contract (the CLI command surface + shape-spec
format), so they live in one repo: atomic commits, one issue tracker, lockstep
review. The skill drives the engine via `uvx` — see [Wiring](#wiring).

---

## Why PyPI (engine)

`mcp-client-kit` is a general-purpose tool ("typed Python wrappers for any MCP
server"). Audience is wider than one org. PyPI gives `uv add mcp-client-kit`
discoverability, no special repo access, and standard release semantics.

**Alternatives dismissed:**

| Option | Why not |
|---|---|
| Private Artifactory / GitLab PyPI | Unnecessary for a public tool; adds gate-keeping with no benefit. |
| Copier / Cookiecutter template | That's project templating, not library distribution — different problem. |
| git+https-only (no PyPI) | Fine for pre-release pinning; not a long-term distribution strategy. |

**PyPI name status:** `mcp-client-kit` confirmed unclaimed as of 2026-06-14 — claim early.

---

## Wiring: how the skill reaches the engine

The skill does **not** bundle the engine. `SKILL.md` instructs Claude to run the
CLI via `uvx`, which fetches the package from PyPI on demand — no pre-install step
for the user:

```
uvx "mcp-client-kit==0.2.0" codegen …
```

So: **PyPI publish** makes the engine reachable; **repo-as-plugin** delivers the
skill; **the skill's `uvx` pin** is the link. The pin is *exact* (`==`) so an old
skill install can never silently drive a newer CLI (no skew through `uvx`).

(`uvx` assumes the user has `uv`. Claude Code users mostly do; README documents the
`pip install mcp-client-kit` + `mcp-kit` on PATH fallback.)

---

## Versioning: two independent numbers

The engine and the skill version **independently**, because a skill-only change
(SKILL.md prose, better examples) must not force a republish of byte-identical
Python code, and an engine refactor must not force a skill re-tag.

| Number | Source of truth | Bumps when | Registry |
|---|---|---|---|
| **engine** | `pyproject.toml` `version` | `mcp_client_kit/**` code changes | PyPI |
| **product** | `.claude-plugin/plugin.json` `version` | any release (skill or engine) | marketplace (git tag) |
| **engine pin** | `SKILL.md` `uvx mcp-client-kit==<engine>` | tracks engine version | — |

What you are actually versioning is **the contract** — the CLI surface + shape-spec
format. SemVer the *engine* against it (minor = surface change, patch = bugfix,
major = break post-1.0). The *product* version is just the user-facing release
counter.

The two drift apart over time (product release-count ≥ engine release-count). That
is expected and fine.

---

## Tagging: prefixed, per-artifact

One git-tag namespace would force a choice that confuses one audience. Avoid it —
**each artifact gets the tag form its registry's convention expects:**

| Artifact | Git tag | Maps to | Why this form |
|---|---|---|---|
| **engine** | bare **`vX.Y.Z`** | `pyproject.version` ⟷ PyPI release | Honors the universal Python convention `git tag vX.Y.Z == PyPI X.Y.Z`. Every bare tag a visitor sees **is** on PyPI — no gaps, no "where's 0.1.1?". |
| **plugin** | **`plugin-vX.Y.Z`** | `plugin.json` version ⟷ marketplace `ref` | Clearly a separate, labeled namespace — not a missing PyPI release. |

The engine — the thing a repo visitor assumes the repo *is* (it's a Python package
named `mcp-client-kit`) — keeps the bare tags. The skill takes the prefix.

**README must set this expectation explicitly:**

> This repo ships two artifacts: the `mcp-client-kit` Python package (PyPI; git tags
> `vX.Y.Z`) and the `mcp-client-kit` Claude Code plugin — which provides the
> `generate-mcp-wrappers` skill (git tags `plugin-vX.Y.Z`).

---

## Release flows

**Engine changed** (code under `mcp_client_kit/**`):

```bash
# bump pyproject.toml version, update SKILL.md pin to match, bump plugin.json
git commit -am "release: engine v0.2.0"
git tag v0.2.0
git push && git push --tags          # 'v*' tag fires the publish workflow
# in agent-skills repo: bump the entry ref to v0.2.0 (or plugin-v0.2.0 if also re-tagged)
```

**Skill-only changed** (SKILL.md, docs — no engine code):

```bash
# bump plugin.json only; pyproject + SKILL.md pin unchanged
git commit -am "release: plugin v0.1.1 (engine unchanged 0.1.0)"
git tag plugin-v0.1.1
git push && git push --tags          # 'plugin-v*' does NOT fire publish — no PyPI involvement
# in agent-skills repo: bump the entry ref to plugin-v0.1.1
```

Separated triggers mean a skill-only release simply never runs the publish job —
no phantom PyPI version, no skip-existing guard needed.

**Timeline (watch the numbers drift):**

```
Release 1  initial            engine 0.1.0   tag v0.1.0         → publish engine 0.1.0
Release 2  SKILL.md prose      engine 0.1.0   tag plugin-v0.1.1 → no publish; pin stays ==0.1.0
Release 3  new CLI flag        engine 0.2.0   tag v0.2.0        → publish engine 0.2.0; pin → ==0.2.0
```

---

## Release workflow (GitHub Actions)

Publish fires **only on bare `v*` tags** (engine releases) via uv Trusted
Publishing (OIDC — no API token in secrets):

```yaml
name: Publish engine

on:
  push:
    tags: ["v[0-9]+.[0-9]+.[0-9]+"]   # bare engine tags only; 'plugin-v*' excluded

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi                 # required for Trusted Publishing
    permissions:
      id-token: write                 # OIDC token for uv publish
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv build
      - run: uv publish
```

**One-time setup:** register a Trusted Publisher on PyPI (Settings → Publishing →
Add a new pending publisher). Fields: GitHub owner, repo, workflow filename,
environment (`pypi`). Reference:
[astral-sh/trusted-publishing-examples](https://github.com/astral-sh/trusted-publishing-examples).

**Dry run first:** register on TestPyPI and publish there
(`uv publish --index-url https://test.pypi.org/legacy/`) to validate end-to-end
before the real release.

---

## CI guard: pin must reference a real engine

Independent of the tag scheme, assert at release that the skill's pin points at the
engine version actually in `pyproject.toml`, so the skill can never ship pointing
at a non-existent engine:

```
SKILL.md pin (mcp-client-kit==X.Y.Z)  ==  pyproject.toml version
```

A ~5-line check (grep both, compare). Fail the release on mismatch. The product /
`plugin.json` version is free to differ — it is only the release counter.

---

## Discovery: aggregate via agent-skills, keep code co-located

The skill stays in this repo (co-located with its engine). It is surfaced through
the existing **`svd-agent-skills`** public marketplace as an **external-source
entry** — the marketplace lists a pointer, it does not vendor a copy:

```json
{
  "name": "mcp-client-kit",
  "source": { "source": "github", "repo": "<owner>/mcp-client-kit", "ref": "plugin-v0.1.1" },
  "description": "Generate typed Python wrappers for any MCP server (skill: generate-mcp-wrappers)."
}
```

(Marketplace entry `name` is the **plugin** name = `mcp-client-kit`. The skill it
provides is `generate-mcp-wrappers`.)

(`git-subdir` source + `path` if the plugin sits in a subdir rather than repo root.
The Claude Code marketplace schema supports `url`, `github`, `git-subdir`, and `npm`
sources with `ref`/`sha` pinning.)

Result: **one marketplace for users to add** (centralized discovery), **code +
engine in one repo** (atomic commits, one tracker, lockstep). Bump the skill → move
the `ref`.

**Optional dual discovery:** this repo can also carry its own
`.claude-plugin/marketplace.json` so it is installable standalone
(`/plugin marketplace add <owner>/mcp-client-kit`) for anyone landing on the repo
directly. Coexists with the aggregator entry.

---

## To make this repo a plugin (small, not yet done)

- `.claude-plugin/plugin.json` — `name: mcp-client-kit`, `version` (= product),
  `description`. **Not** `skills`/`agents` — the `generate-mcp-wrappers` skill is
  auto-discovered from `skills/`.
- (optional) root `.claude-plugin/marketplace.json` for standalone install.

---

## Pre-publish checklist (gate before first public release)

REQUIRED before first publish — not scope for the doc/versioning work, called out so
they don't get skipped:

- [x] **Genericize internal references.** All org-specific server names, endpoints,
  and internal doc references replaced with neutral `example.com` examples throughout
  code, tests, SKILL.md, and public docs. Three internal-only eval docs excluded via
  `.gitattributes export-ignore`; remove them from the repo before first public push.
- [ ] **Add `LICENSE`** (MIT or Apache-2.0 — pick one).
- [ ] **Fill `pyproject.toml` metadata:** `authors`, `license`, `readme = "README.md"`,
  `classifiers`, `[project.urls]` (Homepage, Source, Issues).
- [ ] **Add `.claude-plugin/plugin.json`** (product version) so the repo is a plugin.
- [ ] **Pin + guard:** SKILL.md uses `uvx "mcp-client-kit==<engine>"`; add the
  pin-equality CI check.
- [ ] **TestPyPI dry run** — validate build + publish before the real index.
- [ ] **Tag strategy** — decide `0.0.x` pre-release vs jump to `0.1.0`; document in README.

---

## Summary

| Concern | Answer |
|---|---|
| Where | one public repo (monorepo): PyPI engine + marketplace skill |
| Versioning | two independent numbers — engine (pyproject→PyPI), product (plugin.json→tag) |
| Tags | engine `vX.Y.Z` (PyPI convention); skill `plugin-vX.Y.Z` |
| Publish trigger | bare `v*` tags only; `plugin-v*` never publishes |
| Skill→engine link | `uvx "mcp-client-kit==<engine>"`, exact pin; CI asserts pin == pyproject |
| Discovery | `svd-agent-skills` marketplace external-source entry; code stays here |
| Publish auth | uv Trusted Publishing (OIDC) — no stored tokens |
| First gating task | ~~genericize internal references~~ ✅ done |
