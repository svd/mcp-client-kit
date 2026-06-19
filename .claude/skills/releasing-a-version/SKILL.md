---
name: releasing-a-version
description: Use when releasing, cutting, or shipping a new version of mcp-client-kit — publishing to PyPI, bumping the version for release, or tagging vX.Y.Z / plugin-vX.Y.Z. Covers the engine (PyPI) and plugin-only (marketplace) flows, including the dev-branch dev0→stable→PR→tag→minor+dev sequence.
---

# Releasing a version

Two artifacts ship from one repo. Full rationale in `doc/DISTRIBUTION.md`.

| Artifact | Version file | Tag form | Fires PyPI | Tag branch |
|---|---|---|---|---|
| **engine** | `pyproject.toml` | `vX.Y.Z` | yes | `main` |
| **plugin** | `.claude-plugin/plugin.json` | `plugin-vX.Y.Z` | no | `main` |

**Pick the flow:**
- `mcpgen/**` code changed → **Engine release**
- Skill/docs/SKILL.md only → **Plugin-only release**
- Both changed → run Engine release first (it implicitly bumps the product too)

---

## Engine release

Run from the `dev` branch.

**1. Precheck**
```bash
git checkout dev && git pull
git status          # must be clean
gh pr checks        # CI green
```
`CHANGELOG.md` must have a `## [Unreleased] — X.Y.Z` entry ready. If not, add one.

**2. Bump to stable**
```bash
uv version --bump stable --dry-run   # preview: 0.2.0.dev0 => 0.2.0
uv version --bump stable             # writes pyproject.toml + uv.lock
```

**3. Finalize CHANGELOG**
Change the unreleased header to a dated release header, e.g.:
```
## [Unreleased] — 0.2.0
```
→
```
## [0.2.0] — 2026-06-20
```
Add a new blank `## [Unreleased] — 0.3.0` placeholder at the top (you'll fill it during the post-release step).

**4. Commit + PR → main**
```bash
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "release: engine v0.2.0"
git push origin dev
gh pr create --base main --title "release: engine v0.2.0"
# merge the PR (squash or merge commit)
```

**5. Tag on main**
```bash
git checkout main && git pull
git tag v0.2.0
git push --tags   # bare v* tag triggers publish.yml → PyPI
```
`publish.yml` asserts the tag (minus `v`) matches `pyproject.toml version` — if they differ the job fails. Fix by ensuring step 2 ran on the same commit.

**6. Re-advance dev**
```bash
git checkout dev
uv version --bump minor              # 0.2.0 → 0.3.0
uv version --bump dev                # 0.3.0 → 0.3.0.dev0
# Add fresh unreleased section to CHANGELOG if not already there:
# ## [Unreleased] — 0.3.0
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore: bump version to 0.3.0.dev0"
git push origin dev
```

---

## Plugin-only release

Use when only skill files, SKILL.md, or docs changed — no engine code.

**1. Bump plugin product version on dev**

Files to update:
- `.claude-plugin/plugin.json` → `"version"` field
- `.claude-plugin/marketplace.json` → `"plugins"[0]["version"]` field only  
  *(leave top-level `"version"` — that's the catalog version, bumped only when the catalog listing itself changes, not per plugin release)*

Optionally raise the version floor in `skills/generate-mcp-wrappers/SKILL.md` — but only if the skill now requires a CLI feature from a newer engine. Two coupled spots:
- Line with `mcpgen >= X.Y.Z` in the prose
- `min=X.Y.Z` in the bash version check below it

**2. Commit + PR → main**
```bash
git add .claude-plugin/
git commit -m "release: plugin v0.1.1 (engine unchanged 0.2.0)"
git push origin dev
gh pr create --base main --title "release: plugin v0.1.1"
# merge the PR
```

**3. Tag on main**
```bash
git checkout main && git pull
git tag plugin-v0.1.1
git push origin plugin-v0.1.1   # plugin-v* does NOT trigger PyPI publish
```

**4. Update the marketplace entry**
In the `svd-agent-skills` marketplace repo, bump the `mcp-client-kit` entry's `ref` to `plugin-v0.1.1`. This points consumers at the stable `main` tag, not a dev pre-release.

---

## Common mistakes

| Mistake | Effect |
|---|---|
| Tag on `dev` instead of `main` | Marketplace/PyPI points at a dev pre-release |
| Tag version ≠ `pyproject.toml` version | `publish.yml` fails the tag-vs-version assertion |
| Skip step 6 (re-advance dev) | dev stays at release version, no `.dev0` signal |
| Bump `pyproject.toml` for plugin-only | Triggers unnecessary PyPI republish |
| Bump `marketplace.json` top-level `version` for every plugin release | Top-level is catalog metadata, not plugin version — leave it unless the catalog listing changed |
| Raise SKILL.md floor preemptively | Floor should only move when the skill actually needs the new engine feature |
