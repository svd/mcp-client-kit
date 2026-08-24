# Design: external-audit Milestones 1 & 2

**Source:** an external audit of v0.5.0 at commit `4ecd44f`, held outside this repository. Section references below (`audit §N`) point into that document; everything this design depends on is restated here, so the spec stands alone.
**Scope:** audit §10 Milestone 1 (trust) and Milestone 2 (performance/operability). Milestone 3 is out of scope.
**Definition of done:** the 12-item acceptance checklist in audit §11, restated in Verification below.

## Goal

Two outcomes:

1. **Trust.** A team can commit generated wrappers plus a manifest and have CI reliably catch input-schema tool drift, with documentation that does not overclaim.
2. **Operability.** A series of generated calls runs inside one connection lifecycle instead of reconnecting per tool call.

Non-goals, from audit §2: this does not become a general MCP client framework. Generated wrappers stay static. `seam.py`'s `McpCaller` Protocol is unchanged — changing it breaks every generated file.

## Settled decisions

These were open in the audit and are now fixed.

| Decision | Choice |
|---|---|
| Manifest filename | `<out-stem>.mcpgen.json`, derived from `--out` (not from the server name) |
| Manifest write policy | Always, on any `codegen` run that has `--out`; `--manifest PATH` overrides, `--no-manifest` suppresses |
| `description` changes | Advisory — reported, never affects exit code |
| Session-reuse API | `caller.connected()`, per-server lazy session map (not `McpBridgeCaller.connect()`) |
| Concurrency in a block | Lock guards session *open*; `call_tool()` runs unserialized |
| SSE | Mark `probeable=False` with a note now; no adapter this round |
| Credential-backend default | `file` — fix `doc/USAGE.md` to match the code |

Two deliberate deviations from the audit's literal text, both approved:

- **Manifest carries no `generated_at` or `generator_version`.** The audit's example JSON has both. A timestamp makes every regeneration a git diff even when nothing changed, and `generator_version` dirties every manifest on a version bump; both contradict acceptance item 4 ("writes or updates a non-secret manifest **deterministically**"). `format_version` alone covers format migration. Manifest content is a pure function of `(server, tools)`.
- **Session reuse uses a dedicated owner task**, not a bare `AsyncExitStack` on the caller. Rationale in §5.

---

## §1 — `mcpgen/manifest.py` (new module, pure, no I/O)

A new module rather than additions to `codegen.py`: the audit suggests this (§3 "Целевые файлы") and `codegen.py` is already 906 lines with a distinct job (JSON Schema → Python source).

### Public surface

```python
FORMAT_VERSION = 1

def build(server: str, tools: list[dict]) -> dict
def canonical(obj: Any) -> Any
def diff(old: dict, new: dict) -> DriftReport
def render_text(report: DriftReport) -> str
def to_json(report: DriftReport) -> dict
```

`tools` is the list of dicts already produced by `cli._list_tools()` — each `{"name", "description", "inputSchema", "annotations"}`.

### Manifest format

```jsonc
{
  "format_version": 1,
  "server": "demo",
  "tools": {
    "add": {
      "description": "Add two numbers",
      "inputSchema": { "type": "object", "properties": { … }, "required": ["a", "b"] },
      "annotations": null
    }
  }
}
```

Serialized with `json.dumps(..., indent=2, sort_keys=True)` plus a trailing newline. Non-secret by construction: every value comes from the server's `tools/list` response. No URLs, tokens, env vars, or local paths.

### Canonicalization

`canonical()` normalizes so that a semantically identical inventory produces an identical structure:

- object keys sorted recursively (also enforced by `sort_keys=True` at dump time, but `diff()` compares structures, not strings, so it must not rely on dump order);
- tools sorted by name;
- **array order is preserved in general** — `"prefixItems"`, `"allOf"` and similar are positional;
- **exception:** `required` and `enum` are compared as sets, since `["a","b"]` and `["b","a"]` are the same constraint. This is what makes "canonical ordering does not cause a false positive" (audit §3 required test) true rather than aspirational.

### `DriftReport`

```python
@dataclass(frozen=True)
class DriftReport:
    added: list[str]                       # tool names present live, absent in manifest
    removed: list[str]                     # present in manifest, absent live
    changed: list[ToolChange]              # inputSchema and/or annotations differ
    advisory: list[ToolChange]             # description-only differences
    @property
    def has_drift(self) -> bool            # bool(added or removed or changed)
```

`ToolChange` carries the tool name, a category (`required`, `enum`, `input_schema`, `annotations`, `description`), and old/new fragments for display. A tool whose `inputSchema` *and* `description` both changed appears in `changed` only — advisories are description-only differences, so an advisory never hides real drift.

`diff()` refuses a manifest whose `format_version` it does not recognize, raising `ValueError` (surfaces as exit 2, not as drift).

### Human-readable output

```text
ADDED     demo.subtract
REMOVED   demo.legacy_add
CHANGED   demo.add: required properties changed: +precision
CHANGED   demo.greet: enum changed for 'style': -formal +casual
ADVISORY  demo.add: description changed
            - "Add two numbers"
            + "Adds two integers and returns the sum"

Drift detected: 1 added, 1 removed, 2 changed (1 advisory).
```

Clean run with an advisory: `No drift. (1 advisory)`.

---

## §2 — manifest emission from `codegen`

`cli._cmd_codegen` writes the manifest after writing `--out`.

- Default path: `--out gen/demo.py` → `gen/demo.mcpgen.json`. Deriving from the out-stem rather than the server name keeps the pair together when the module name differs from the server name, and keeps the manifest inside the directory the user chose.
- `--manifest PATH` overrides the derived path.
- `--no-manifest` suppresses the write.
- No `--out` (stdout mode) → no manifest, no warning.
- Written atomically via the existing `_atomic_write_text` helper.
- Reported on stderr like the other artifacts: `[codegen] wrote gen/demo.mcpgen.json (4 tools)`.

The generated `.py` output is unchanged byte-for-byte; the existing `test_codegen.py` corpus must stay green untouched.

---

## §3 — `mcpgen check`

```
mcpgen check <server> [--manifest PATH] [--json] [--update] \
    [--stdio CMD] [--url URL] [--bearer TOKEN] [--client-name NAME] \
    [--config PATH] [--creds PATH] [--cred-backend {file,keyring,auto}] [--env KEY[=VAL]]
```

Connection flags come from the existing `_add_conn_args()` plus the `--stdio` flag, so `check` and `codegen` accept exactly the same connection surface by construction.

`--manifest` defaults to `<server>.mcpgen.json` in the working directory.

### Flow

1. Read and parse the manifest file.
2. Open a live session and call `tools/list` (reuses `cli._list_tools`).
3. `manifest.build()` the live inventory, `manifest.diff()` against the stored one.
4. Render text (default) or `--json`.
5. If `--update`, write the fresh manifest.
6. Exit.

**No probing.** `check` never calls `call_tool` and never touches `.shapes.json`. This is acceptance item 5.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No drift. Advisories may have printed. Also the `--update` success code. |
| 1 | Drift: a tool was added, removed, or had its `inputSchema`/`annotations` change. |
| 2 | Operational: manifest missing or unparseable, unknown `format_version`, server not found in config, transport failure, auth failure (`ReauthenticationRequired`), SSE-unsupported. |

Every connection, config, and auth exception maps to 2, never to 1. A CI runner with a broken token or an unreachable server must not report as schema drift — this is the single most important behavior in the command, and it gets a dedicated test.

Note that `check` deviates from the other subcommands here: `_cmd_codegen` and `_cmd_list` return 1 on `FileNotFoundError`/`ValueError`. `check` reserves 1 exclusively for drift, per audit §3.

### `--update`

Writes the freshly built manifest to the manifest path and exits **0**, printing what it accepted:

```text
CHANGED   demo.add: required properties changed: +precision
[check] updated demo.mcpgen.json (accepted 1 change)
```

This is the only path that writes a manifest outside `codegen`, and it requires the explicit flag. Without `--update`, `check` never writes. `--update` on an operational error (exit 2) writes nothing.

### `--json`

```jsonc
{
  "server": "demo",
  "drift": true,
  "added": ["subtract"],
  "removed": ["legacy_add"],
  "changed": [ { "tool": "add", "category": "required", "detail": "…", "old": …, "new": … } ],
  "advisory": [ { "tool": "add", "category": "description", "old": "…", "new": "…" } ]
}
```

On exit 2 the JSON form emits `{"server": …, "error": "…"}` so a CI job parsing stdout does not choke.

### Required tests (audit §3), in `tests/test_cli_check.py`

Unit tests with `_list_tools` mocked, matching the existing `tests/test_cli_list.py` style:

1. no drift → exit 0, no output noise
2. added tool → exit 1
3. removed tool → exit 1
4. changed required parameter → exit 1
5. changed enum → exit 1
6. canonical ordering (reordered object keys, reordered `required`/`enum`) → exit 0, no false positive
7. transport/auth failure → exit 2, not 1, and no false drift text
8. missing/unparseable manifest → exit 2
9. `--update` absent → manifest file unmodified even when drift exists
10. `--update` present → manifest rewritten, exit 0
11. `--json` shape on both drift and error paths
12. description-only change → exit 0 with an advisory line

---

## §4 — SSE gating, credential default, documentation

### SSE (audit §6.3, variant 1)

`discovery.py` classifies servers as `transport="sse"` at `_build_server_from_get` (~:211-240) and `_entry_to_server` (~:371-407), but `_bridge.session()` has no SSE branch, so such a URL falls into the Streamable HTTP path and the connection fails. `probeable: bool` already exists on `DiscoveredServer` (:68) and `cli.py:569` already gates the runnable hint on it, so the fix is small.

- Both classification sites set `probeable=False` and `note="SSE transport is discovered but not supported by this mcpgen version"` when `transport == "sse"`.
- `_bridge._parse_servers()` gains a fifth return value, `{name: type}`, collecting the config's declared `type`. `session()` raises `ValueError("server 'x' uses SSE transport, which this mcpgen version does not support")` before attempting a connection when the config declares `type: "sse"` and no `--stdio`/`--bearer` override is present.
- **Known limit, documented not hidden:** an inline `--url` carries no transport type, so an SSE URL passed inline still fails at the transport layer with the SDK's own error. Detecting SSE from a URL alone would be a guess.
- Docs say "MCP servers reachable via stdio and Streamable HTTP", never "any MCP server".

### Credential-backend default (audit §6.4)

`_bridge.resolve_cred_backend()` returns `"file"` (:144) and `--cred-backend`'s CLI help already says "file (default…)" (`cli.py:811`). `README.md` makes no default claim. Only `doc/USAGE.md` is wrong, at lines ~205, ~211 and ~260. Fix the doc; no code change, so nobody's stored credentials relocate.

### Distribution-path contradiction (audit §6.4, second half)

`doc/USAGE.md:18` claims the skill runs the engine via `uvx` with "no separate engine install needed". `skills/generate-mcp-wrappers/SKILL.md:67` hard-fails when `mcpgen` is absent from `PATH`. README is correct; USAGE is wrong. Correct USAGE.

### README changes (audit §6, §7)

- Decouple mcpgen's value from the 98% token-reduction claim: mcpgen generates the wrapper layer for code-mode architectures; savings accrue when a runtime executes those wrappers instead of loading the full catalog. mcpgen does not alter host tool registration and does not provide a sandbox.
- New **"What mcpgen does not do"** section: does not remove tool schemas from Claude Code/Cursor context; does not sandbox generated code; does not infer trustworthy output types from input schemas alone; does not guarantee a remote server stays stable.
- Lifecycle table on the first page:

  | Step | Result |
  |---|---|
  | `codegen` | typed inputs, `Any` return by default |
  | `probe` | observed shape skeleton |
  | edit shape-spec / skill | output model + unwrap decisions |
  | regenerate | `TypedDict` returns, overloads |

  Framing: "Typed input wrappers by default; empirically shaped output types after live probes."
- Discovery copy narrowed to "Discover MCP servers configured in Claude Code" (audit §7 variant A). `PROVIDERS` stays a documented extension point.
- `mcpgen check` workflow plus a CI example (exit-code contract, `--json`).
- `--check` moves out of the roadmap section.

`doc/USAGE.md` gets the corresponding `check` reference section.

---

## §5 — session reuse in `_bridge.py`

### Current behavior

`McpBridgeCaller.call()` (`_bridge.py:1009`) opens a fresh `session(...)` per invocation. Every wrapper call re-runs connection setup: a new stdio subprocess, or a new HTTP connection plus OAuth pre-flight refresh and `initialize()`.

### API

```python
caller = McpBridgeCaller(cmd="python server.py")

async with caller.connected():
    me = await gh.get_me(caller)                    # opens + initializes once
    a, b = await asyncio.gather(                    # concurrent, one session
        gh.get_issue(caller, number=1),
        gh.get_issue(caller, number=2),
    )
# block exit: sessions closed, subprocess reaped

await gh.get_me(caller)                             # outside: one-shot, unchanged
```

`connected()` is an `asynccontextmanager` method on the existing class — no second entity, `__init__` untouched, `seam.py` untouched. Generated wrappers still see only `McpCaller.call()`.

### Why an owner task rather than a plain `AsyncExitStack`

The obvious implementation — hold an `AsyncExitStack` on the caller and `enter_async_context(session(...))` on first use — breaks under the chosen concurrency model. `stdio_client` and `streamable_http_client` open anyio task groups internally, and anyio requires a cancel scope to be exited in the task that entered it. If a lazy session opens inside an `asyncio.gather()` child task and the block later closes in the parent task, anyio raises *"Attempted to exit cancel scope in a different task"*.

So `connected()` spawns a **session-owner task** that owns the `AsyncExitStack` for the block's lifetime:

- Open requests are sent to the owner over an `asyncio.Queue`; the owner performs every `__aenter__` and returns the session (or the exception) over a per-request future.
- Block exit signals the owner, which unwinds the stack in its own task — the same path for the normal and the exception case.
- All `__aenter__`/`__aexit__` calls therefore happen in one task, whatever task issued the call.

### Semantics

1. **One-shot unchanged.** Outside a block, `call()` behaves exactly as today. This is acceptance item 6 and is protected by the existing `tests/test_bridge.py` suite passing untouched.
2. **Per-server lazy map.** The block holds `dict[str, ClientSession]` keyed by the `server` argument. A session opens on first use of that name and is reused for the rest of the block. A config-file-backed caller can legitimately reach two configured servers in one block.
3. **No silent reuse across incompatible args.** Connection arguments (`cmd`/`url`/`bearer`/`config_path`/`creds_path`/`env`) are fixed per caller instance, so within one block the key `server` fully determines the connection. Two different callers never share sessions.
4. **Lock on open.** An `asyncio.Lock` guards the open path so two concurrent first-calls to the same server cannot start two subprocesses. `call_tool()` itself runs unserialized — the SDK's `ClientSession` multiplexes on JSON-RPC request id — so `asyncio.gather()` over wrappers stays genuinely concurrent.
5. **Cleanup on the exception path.** An exception inside the `async with` propagates after the owner has unwound the stack. Acceptance item 7.
6. **One `initialize()` per server per block**, one stdio subprocess per block, one OAuth pre-flight refresh per block (it lives inside `_http_session`, which now runs once per block rather than once per call).
7. **Nested `connected()` raises `RuntimeError`.** No reference counting, no ambiguity about which block owns a session.

### Tests (`tests/test_bridge.py`)

- Two calls in one block → exactly one `ClientSession.initialize()` (fake transport).
- Two calls outside a block → two `initialize()` calls (one-shot regression guard).
- Exception inside the block → session closed, stack unwound.
- Concurrent `gather()` of two first-calls to the same server → one session opened, both results returned.
- Concurrent calls opened from child tasks → no anyio cancel-scope error (this is the test that justifies the owner task).
- Two distinct server names in one block → two sessions, both closed.
- Nested `connected()` → `RuntimeError`.
- Stdio subprocess started once per block.

---

## §6 — runner templates

`skills/generate-mcp-runner/runner_templates/{stdio,http_bearer,http_oauth,http_public}.py` each construct an `McpBridgeCaller` then interpolate `$demo_calls`. Each template wraps the demo calls in `async with caller.connected():`, and the surrounding `SKILL.md` guidance is updated for the extra indentation level `$demo_calls` now needs.

```python
async def main() -> None:
    caller = McpBridgeCaller(cmd="$launch")

    async with caller.connected():
$demo_calls
```

Acceptance: "Runner templates use the reuse API" (audit §4).

---

## §7 — `tests/integration/` (audit §5)

```text
tests/integration/
  fixtures/stdio_server.py
  test_stdio_e2e.py
  test_generated_wrapper_e2e.py
```

### Fixture server

A real MCP server over stdio, built on the `mcp` SDK already in the dependency set:

- `greet(name: str, excited: bool = False) -> {message, length}`
- `add(a: int, b: int) -> int`
- `list_records(limit: int = 3) -> list[{id, name}]` — list-of-records shape
- `json_payload() -> str` — a JSON string payload, for the parse path
- `styled(name: str, style: Literal["formal","casual"])` — enum input schema, exercised by the `check` enum-drift path

### Cases

1. `codegen` via `--stdio` produces an importable module **and** a manifest; `check` against that manifest exits 0.
2. `probe → merge → hand-set shape → codegen` produces a `TypedDict` return annotation.
3. A generated wrapper invokes the real tool and gets the expected response.
4. Two wrapper calls inside one `connected()` block complete in one connection lifecycle.
5. A mutated fixture server (one tool's `required` changed) makes `check` exit 1 — a live end-to-end drift detection, not just a unit test.

These run in default CI: stdio is local, deterministic, and needs no secrets. Remote public endpoints stay out of default CI per audit §5 — they are flaky, rate-limited, and may need OAuth. The wheel-install smoke test belongs to the release workflow, not to this suite.

---

## Verification

- `uv run pytest` — the 322-test baseline stays green, plus the new `check`, bridge-reuse, and integration tests.
- `uv run ruff check mcpgen/`
- `uv run mypy`

### Definition of done — acceptance checklist (audit §11)

Work is complete when every item holds, and the final report addresses each one individually, marking anything not achieved.

1. The existing 322+ tests still pass.
2. New `check` tests cover no drift, add, remove, schema change, canonical order, and errors.
3. `mcpgen check` has stable, documented exit codes.
4. `codegen` writes or updates a non-secret manifest deterministically.
5. No live probing occurs as a side effect of the default drift check.
6. `McpBridgeCaller` supports explicit connection reuse without breaking one-shot use.
7. A reused session cleans up after errors.
8. An E2E stdio fixture covers generated-wrapper invocation.
9. Documentation states the default credential backend consistently.
10. Documentation separates mcpgen's value from host/runtime code-mode token savings.
11. Documentation does not overclaim SSE, multi-host discovery, or universal MCP compatibility.
12. CI keeps lint, format, typecheck, and test checks green.

## Out of scope

- Milestone 3 entirely: benchmarks, example folders, external users.
- A real SSE transport adapter (audit §6.3 variant 2).
- SDK compatibility matrix / CI test matrix (audit §8).
- Version bump and CHANGELOG entry — a separate release flow.
- Any change to `seam.py`'s `McpCaller` Protocol.
