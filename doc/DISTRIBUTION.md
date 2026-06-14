# Distribution plan: mcp-client-kit

**Decision:** public GitHub repo + PyPI. Not internal-only.

## Why PyPI

`mcp-client-kit` is a general-purpose tool ("typed Python wrappers for any MCP
server"). The audience is wider than one org. PyPI gives:

- `uv add mcp-client-kit` / `pip install mcp-client-kit` discoverability.
- No special repo access for contributors or early adopters.
- Standard release semantics (tags → versions → changelogs).

**Alternatives dismissed:**

| Option | Why not |
|---|---|
| Private Artifactory / GitLab PyPI | Unnecessary for a public tool; adds gate-keeping with no benefit. |
| Copier / Cookiecutter template | That's project templating, not library distribution — different problem. |
| git+https-only (no PyPI) | Fine for pre-release pinning; not a long-term distribution strategy. |

**PyPI name status:** `mcp-client-kit` confirmed unclaimed as of 2026-06-14 — claim early.

---

## Install snippet

```bash
# released — from PyPI
uv add mcp-client-kit           # or: pip install mcp-client-kit

# unreleased / pre-tag — tag-pinned from public GitHub (uv.lock pins exact commit)
uv add "mcp-client-kit @ git+https://github.com/<owner>/mcp-client-kit.git@v0.1.0"
```

The git+https form produces a reproducible install: `uv.lock` records the exact
commit SHA behind the tag, so `uv sync` is deterministic even if the tag is later
moved.

---

## Release flow

1. Bump `version` in `pyproject.toml`.
2. `git tag v<X.Y.Z> && git push origin v<X.Y.Z>`.
3. GitHub Actions runs `uv build` + `uv publish` via **Trusted Publishing** (OIDC
   — no API token stored in secrets).

Minimal workflow (`.github/workflows/release.yml`):

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi           # required for Trusted Publishing
    permissions:
      id-token: write           # OIDC token for uv publish
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv build
      - run: uv publish
```

**One-time setup:** register a Trusted Publisher on PyPI (Settings → Publishing →
Add a new pending publisher). Fields: GitHub owner, repo name, workflow filename,
environment name (`pypi`). Reference:
[astral-sh/trusted-publishing-examples](https://github.com/astral-sh/trusted-publishing-examples).

**Dry run first:** swap `uv publish` for `uv publish --index-url https://test.pypi.org/legacy/`
and register on TestPyPI to validate the workflow before the real release.

---

## Pre-publish checklist (gate before v0.1.0)

These are REQUIRED before first publish — not scope for the doc-reconciliation
session, calling them out explicitly so they don't get skipped:

- [ ] **Genericize internal references.** README, docs, and eval scripts still
  cite a real corporate server ("EPAM radar") as the validation target, and
  `servers.example.json` uses internal naming. Replace all org-specific examples
  with `example.com` / a public demo MCP server (e.g. a local stdio server).
  This is a real editing pass, not a one-liner.
- [ ] **Add `LICENSE`** (MIT or Apache-2.0 — pick one).
- [ ] **Fill `pyproject.toml` metadata:** `authors`, `license`, `readme =
  "README.md"`, `classifiers`, `[project.urls]` (Homepage, Source, Issues).
- [ ] **TestPyPI dry run** — confirm the build and publish workflow end-to-end
  before touching the real index.
- [ ] **Tag strategy** — decide: `0.0.x` pre-release series vs jump to `0.1.0`
  on first public push; communicate in README.

---

## Summary

| Concern | Answer |
|---|---|
| Where | PyPI (public) + GitHub |
| Auth | uv Trusted Publishing (OIDC) — no stored tokens |
| Build backend | `hatchling` (already in `pyproject.toml`) |
| Lock / reproducibility | `uv.lock` pins exact commit for git+https installs |
| First gating task | Genericize internal EPAM references |
