# Deciding which tools are safe to probe

Probing makes a **real** live call. A tool wrongly cleared gets executed against the server;
a tool wrongly flagged is silently dropped from coverage. Both errors are cheap to avoid and
expensive to discover later, so the order below is fixed: trust the server's own metadata
first, fall back to reading the name and description only where it is silent.

## The keyword test

One shared definition, used by both branches below so the two cannot drift.

**Words:** `create`, `update`, `delete`, `remove`, `send`, `set`, `write`, `post`, `patch`,
`put`, `cancel`, `approve`, `submit`, `assign`, `add`, `append`, `insert`, `upsert`, `push`,
`close`, `revoke`.

Match the **name only, never the description** — prose routinely names mutating verbs while
describing a read ("returns recently created records"). Split the name on `_`, `-`, and
camelCase boundaries and compare **whole words**, so `created` does not match `create` and
`get_address` does not match `add`. The test **passes** (the tool looks mutating) when any
whole word is on the list.

### Read-verb exemption — the one judgment call

When a read verb (`get`, `list`, `read`, `search`, `find`, `fetch`, `query`, `describe`,
`show`, `count`) leads the name — after any server or object prefix, since `board_list_items`
and `sharepoint_folder_search` are the same shape as `list_items` — ask what the matched word
is doing in the name.

Naming *what is read* does not make the tool write it: `list_add_ons`, `get_close_reasons`,
`search_push_events` are reads whose match is a noun. Naming a *second action* does:
`get_or_create_entity`, `find_or_create_page`, `getOrCreateThread` each really can write.

Exempt the first kind; flag the second. This is the only place in the test where you reason
rather than match, so when the reading is genuinely unclear, **flag it** — an over-flagged
tool costs one line in `mutating-skipped`, an under-flagged one gets called for real.

## Fallback order

### 1. Primary — `annotations.readOnlyHint`

If a tool's `annotations` object (from `mcpgen list --schema`) carries `readOnlyHint`, trust
it: `true` → safe to probe, `false` → mutating. Do not re-litigate an agreeing hint with the
keyword test — that over-flags read-only tools and suppresses probing.

**Contradiction check — the one time you do look twice.** Trust the hint unless the server's
own metadata or the tool's name argues against it. Run both tests on every
`readOnlyHint: true` tool; neither costs a live call.

- **Self-contradicting annotations** — `destructiveHint: true` or `idempotentHint: false`
  alongside `readOnlyHint: true`. The server contradicts itself. (`list --schema` passes
  through every annotation field the server sets, so these are already in hand.)
- **Name contradicts hint** — run the keyword test. The hint is disputed when it passes;
  otherwise the hint stands. `create_item`, `user_delete` and `get_or_create_entity` are
  disputed; `list_recently_created_items` and `getDeletedRecords` are not.

On either hit, do **not** silently probe and do **not** silently skip. Treat the tool as
mutating, flag it `⚠ ... [MUTATING — hint disputed]` in the step-2b report, and record the
reason `readOnlyHint: true contradicted by <what>`. It becomes probeable only on an explicit
user yes, exactly like any other mutating tool. An agreeing hint stays trusted and needs no
marker.

### 2. Fallback — keyword heuristic + semantic read

Only when `annotations` is absent or lacks `readOnlyHint` — common; many servers don't set it.

Run the keyword test; a pass flags the tool. Its whole-word, name-only matching is what keeps
`get_address` and `list_closed_issues` unflagged, and that matters in both directions: step 2c
skips every flagged tool, so a false flag drops coverage — the mirror of the miss the list
exists to prevent.

**Then** read the description semantically for mutating verbs no keyword catches ("records
changes", "switches branches", "writes a memo"). The keyword test is a last resort, not a
sufficient safety net on its own — when in doubt, treat as mutating.

## Record the verdict, do not resolve it silently

This applies to any tool flagged as mutating without a `readOnlyHint: false` to say so —
by the fallback above, or by the contradiction check. A tool whose annotations carry no hint
has no third route: it is this fallback's population, and the keyword test is what decides it.
A tool cleared by `readOnlyHint: true` needs no marker.

- **Probed** — it carries `"_mutating_suspect": true` and a one-line `"_mutating_reason"` in
  its shape-spec entry:

  ```json
  {"_mutating_suspect": true, "_mutating_reason": "name contains 'append'; no readOnlyHint"}
  ```

  Add both **in step 4, after `mcpgen merge`**. Merge replaces a re-probed tool's entry
  wholesale rather than unioning its keys, so anything hand-added beforehand is dropped
  without a word — and must be re-added after any later partial re-probe.

- **Skipped** — there is no shape entry to hold it. Record it under a `## mutating-skipped`
  heading in `session-overview.md` with that same one-line reason.

Either way the verdict survives the run.
