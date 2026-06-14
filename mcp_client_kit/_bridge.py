"""Standalone MCP backend — no mcp_client dependency.

Drop-in replacement for the temporary mcp_client bridge. Everything above
this file (generated wrappers, codegen, seam) is unchanged — only this backend
swaps. Auth uses the official mcp SDK OAuthClientProvider with a thin
FileTokenStorage. Pre-flight refresh works around FastMCP bug #3425
(expired-token-looks-fresh-after-reload): we refresh the access token before
opening a session so the SDK always sees a live credential.
"""
from __future__ import annotations

import asyncio
import errno as _errno
import json
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

DEFAULT_CREDS_PATH = Path.home() / ".mcp-client-kit" / "credentials.json"

SERVERS: dict[str, str] = {
    "radar": "https://mcp.example.com/mcp/radar",
    "internal": "https://mcp.example.com/mcp/internal",
}

# Treat a cached token as expired this many seconds before its real expiry.
_MARGIN = 120


class ReauthenticationRequired(Exception):
    """Tokens absent or refresh failed. Run: mcp-kit login <server>"""


class FileTokenStorage(TokenStorage):
    """File-backed OAuth token + client info store, keyed by server name."""

    def __init__(self, server_name: str, credentials_path: Path = DEFAULT_CREDS_PATH) -> None:
        self._key = server_name
        self._path = credentials_path

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))

    async def get_tokens(self) -> OAuthToken | None:
        data = self._load()
        raw = data.get(self._key, {}).get("tokens")
        if raw is None:
            return None
        expires_at = raw.get("expires_at")
        if expires_at is not None and time.time() >= expires_at - _MARGIN:
            # Pre-flight refresh should have run; if still here, treat as absent.
            return None
        return OAuthToken(**raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        serialized = tokens.model_dump(mode="json", exclude_none=True)
        if tokens.expires_in is not None:
            serialized["expires_at"] = int(time.time()) + int(tokens.expires_in)
        data.setdefault(self._key, {})["tokens"] = serialized
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._load()
        raw = data.get(self._key, {}).get("client_info")
        if raw is None:
            return None
        return OAuthClientInformationFull(**raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data.setdefault(self._key, {})["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._save(data)


async def _pre_flight_refresh(server_name: str, storage: FileTokenStorage) -> None:
    """Refresh access token if near/past expiry via plain httpx (no MCP SDK).

    Workaround for FastMCP bug #3425: the SDK doesn't set token_expiry_time on
    cold start, so an expired token passes is_token_valid(). By refreshing before
    opening the session, get_tokens() always returns a live credential.
    """
    data = storage._load()
    entry = data.get(server_name, {})
    tokens_raw = entry.get("tokens") or {}

    expires_at = tokens_raw.get("expires_at")
    if expires_at is None or time.time() < expires_at - _MARGIN:
        return  # token fresh or no expiry info; nothing to do

    refresh_token = tokens_raw.get("refresh_token")
    if not refresh_token:
        raise ReauthenticationRequired(
            f"No refresh_token for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    client_id = entry.get("client_info", {}).get("client_id")
    if not client_id:
        raise ReauthenticationRequired(
            f"No client_id cached for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    token_endpoint = entry.get("token_endpoint")
    if not token_endpoint:
        raise ReauthenticationRequired(
            f"No token_endpoint cached for '{server_name}' (credentials pre-date this version). "
            f"Run: mcp-kit login {server_name}"
        )

    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    client_secret = entry.get("client_info", {}).get("client_secret")
    if client_secret:
        payload["client_secret"] = client_secret

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_endpoint, data=payload)

    if resp.status_code != 200:
        raise ReauthenticationRequired(
            f"Token refresh failed ({resp.status_code}): {resp.text[:200]}. "
            f"Run: mcp-kit login {server_name}"
        )

    await storage.set_tokens(OAuthToken(**resp.json()))


@asynccontextmanager
async def _http_session(server_name: str, server_url: str):
    """OAuth-authenticated HTTP MCP session. Pre-flight refresh before connecting."""
    storage = FileTokenStorage(server_name)
    await _pre_flight_refresh(server_name, storage)

    data = storage._load()
    redirect_uris = data.get(server_name, {}).get("client_info", {}).get("redirect_uris", [])
    callback_uri = redirect_uris[0] if redirect_uris else "http://localhost:0/callback"

    async def _no_browser(url: str) -> None:
        raise ReauthenticationRequired(
            f"OAuth re-auth needed for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    async def _no_callback() -> tuple[str, str | None]:
        raise ReauthenticationRequired(
            f"OAuth re-auth needed for '{server_name}'. Run: mcp-kit login {server_name}"
        )

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name=f"mcp-client-kit ({server_name})",
            redirect_uris=[callback_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=_no_browser,
        callback_handler=_no_callback,
    )

    async with streamablehttp_client(server_url, auth=provider) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


@asynccontextmanager
async def _stdio_session(cmd: str):
    """Stdio MCP session — no auth. cmd is a shell-split command string."""
    parts = cmd.split()
    params = StdioServerParameters(command=parts[0], args=parts[1:])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


@asynccontextmanager
async def session(server: str, *, cmd: str | None = None):
    """Yield an initialized MCP ClientSession.

    cmd: if provided, use stdio transport (no auth).
    server: name in SERVERS dict → HTTP + OAuth; otherwise used as a raw URL.
    """
    if cmd is not None:
        async with _stdio_session(cmd) as s:
            yield s
    elif server in SERVERS:
        async with _http_session(server, SERVERS[server]) as s:
            yield s
    else:
        # Raw URL, no auth
        async with streamablehttp_client(server) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s


class McpBridgeCaller:
    """McpCaller implementation backed by the standalone MCP client."""

    def __init__(self, *, cmd: str | None = None) -> None:
        self._cmd = cmd

    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        async with session(server, cmd=self._cmd) as s:
            result = await s.call_tool(tool, arguments)
            content = [
                {"type": item.type, "text": getattr(item, "text", "")}
                for item in result.content
            ]
            return parse(content)


def parse(content_items: list) -> Any:
    """Extract and JSON-parse the text payload from an MCP tool result."""
    if not content_items:
        raise ValueError("MCP tool result has empty content")
    item = content_items[0]
    text = item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP server error: {text[:300]}") from exc


# ---------------------------------------------------------------------------
# Login flow (browser-based initial OAuth)
# ---------------------------------------------------------------------------

async def _local_callback_server(port: int = 0) -> tuple[int, asyncio.Future]:
    """Start a local HTTP server to receive the OAuth redirect. Returns (port, future)."""
    loop = asyncio.get_event_loop()
    future: asyncio.Future[tuple[str, str | None]] = loop.create_future()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        code: str | None = None
        state: str | None = None
        try:
            data = await reader.read(4096)
            first_line = data.decode(errors="replace").split("\n")[0]
            path = first_line.split(" ")[1] if " " in first_line else ""
            params = parse_qs(urlparse(path).query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            body = b"<html><body><h1>Login complete. You can close this tab.</h1></body></html>"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            if not future.done():
                future.set_result((code, state))

    try:
        server = await asyncio.start_server(_handle, "localhost", port)
    except OSError as exc:
        if exc.errno == _errno.EADDRINUSE and port != 0:
            server = await asyncio.start_server(_handle, "localhost", 0)
        else:
            raise

    actual_port = server.sockets[0].getsockname()[1]

    async def _serve_until_done() -> None:
        async with server:
            await future

    asyncio.create_task(_serve_until_done())
    return actual_port, future


async def login(server_name: str, creds_path: Path = DEFAULT_CREDS_PATH) -> None:
    """Full browser-based OAuth login for server_name. Caches tokens + token_endpoint."""
    if server_name not in SERVERS:
        raise ValueError(f"Unknown server {server_name!r}. Known: {list(SERVERS)}")

    server_url = SERVERS[server_name]
    storage = FileTokenStorage(server_name, creds_path)

    # Clear existing credentials so we start fresh.
    data = storage._load()
    data.pop(server_name, None)
    storage._save(data)

    port, callback_future = await _local_callback_server()
    callback_uri = f"http://localhost:{port}/callback"

    async def redirect_handler(url: str) -> None:
        print(f"\nOpening browser: {url}\n")
        webbrowser.open(url)

    async def callback_handler() -> tuple[str, str | None]:
        print("Waiting for OAuth callback… (complete login in your browser)")
        return await callback_future

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name=f"mcp-client-kit ({server_name})",
            redirect_uris=[callback_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    try:
        async with streamablehttp_client(server_url, auth=provider) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                result = await s.call_tool("whoami", {})
                content = [{"type": item.type, "text": getattr(item, "text", "")} for item in result.content]
                print(f"Logged in as: {parse(content)}")

                if provider.context.oauth_metadata is not None:
                    endpoint_url = str(provider.context.oauth_metadata.token_endpoint)
                    creds_data = storage._load()
                    creds_data.setdefault(server_name, {})["token_endpoint"] = endpoint_url
                    storage._save(creds_data)
    finally:
        if not callback_future.done():
            callback_future.set_result((None, None))
        await asyncio.sleep(0)

    print(f"Credentials saved to {creds_path}")
