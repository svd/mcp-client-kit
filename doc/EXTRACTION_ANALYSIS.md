# Extraction analysis: what to pull out of the origin project

Origin: `mcp_client.py` from the origin project (reviewed 2026-06-12). The generic
layer has since been extracted to `mcp_client_kit/_bridge.py` (official `mcp` SDK backend).

## Layered view of the existing code

The current code already separates cleanly into three layers. Only the bottom
layer is worth extracting; the upper two stay project-specific by design.

### Layer 1 — generic, extractable (`mcp_client.py`)

| Piece | Generic? | Notes for extraction |
|---|---|---|
| `FileTokenStorage` | ✅ | Already keyed by server name. Parameterize the credentials path (now defaults to `~/.mcp-client-kit/credentials.json`). Consider OS keyring as optional backend — plaintext JSON is the main security objection colleagues will raise. |
| `_refresh_token_if_needed` | ✅ | Pre-flight refresh via cached `token_endpoint` — this is the part the official SDK does *not* give you out of the box across process restarts. Core value. |
| `_authenticated_session` | ✅ | Needs `SERVERS` dict injected instead of module-level constant. |
| `call_tool` | ✅ | One-session-per-call is simple but slow for N calls; extracted lib should also expose a session-reuse variant (`async with McpClient(...) as c: await c.call(...)`). |
| `login` + `_local_callback_server` | ✅ | Generic OAuth 2.1 PKCE browser flow. `_LOGIN_VERIFY_TOOL` (whoami/get-current-user) must become a config parameter — it's the only org-specific bit inside. |
| `ensure_authenticated` | ✅ | Generic given injected server registry. |
| `parse_tool_result` | ✅ | Generic MCP envelope unwrap. |
| `ReauthenticationRequired` error messages | ⚠️ | Hardcode `uv run mcp-kit login` — must become a configurable "remediation hint" string. |
| `EARLY_REFRESH_MARGIN_SECONDS`, verbose logging | ✅ | Trivial. |

### Layer 2 — per-server typed wrappers

NOT extractable as code. This is exactly what the proposed **codegen skill**
should produce for any MCP server:

- one `async def` per tool, with docstring from the tool's MCP description
- centralized field projections (origin-project-specific, but the *pattern* —
  "all projections in one module, validated against live responses" — generalizes
  to "all request shaping in one module")
- envelope unwrapping helpers (`_unwrap_entity`, `_unwrap_results`)

### Layer 3 — domain pipeline (`snapshot.py`, `extract.py`, models)

Stays in the origin project. Not part of the reusable story. `snapshot.py`
was reviewed only to confirm it has zero coupling to `mcp_client` internals —
it consumes plain dicts. Good: extraction won't touch it.

## API sketch for the extracted library

```python
from mcp_client_kit import McpClient, ServerConfig

acme = ServerConfig(
    name="acme",
    url="https://mcp.example.com/mcp/acme",   # real endpoints live in user config, never in repo
    verify_tool="whoami",                      # called after login to confirm identity
    login_hint="uv run mcp-kit login acme",   # shown in ReauthenticationRequired
)

client = McpClient(
    servers=[acme],
    credentials_path="~/.mcp-client-kit/credentials.json",  # default
)

# one-shot (current call_tool semantics)
result = await client.call("acme", "get_entity", {...})

# session reuse for bounded-parallel pipelines
async with client.session("acme") as s:
    a = await s.call("get_entity", {...})
    b = await s.call("query_acme", {...})

# CLI surface (console_scripts entry point)
#   mcp-kit login --server acme --config ./servers.toml
```

Auth-optional mode: `ServerConfig(auth=None)` skips OAuth entirely and just
opens `streamablehttp_client(url)` — answers the "is it useful without auth?"
question: yes, but the value shrinks to convenience (one-liner call, envelope
parsing, session mgmt). See VERDICT.md.

## Migration cost in the origin project

- Per-server typed wrappers in the origin project imported from the old
  `mcp_client` module. The extraction is done: the generic layer now lives in
  `mcp_client_kit/_bridge.py` (official `mcp` SDK). A shim keeps the old import
  path working for backward compat.
- `__main__.py` `login` command delegates to the lib.
- Tests: mcp_client has its own behaviors (early-refresh margin, redirect_uri
  mismatch re-registration, EADDRINUSE fallback) that need to move with it.

## Known design debts to fix during extraction (not after)

1. **Plaintext token storage** — add optional `keyring` backend; at minimum
   `chmod 600` the JSON file.
2. **Private API usage** — `storage._load()` is called from outside the class;
   make a public method.
3. **`provider.context.oauth_metadata`** — reaching into SDK internals to cache
   `token_endpoint`; pin `mcp` SDK version range, add a fallback via RFC 8414
   discovery (`/.well-known/oauth-authorization-server`) so refresh works even
   without the cached endpoint.
4. **One session per call** — fine for low-volume use, wasteful generally;
   session-reuse API above.
