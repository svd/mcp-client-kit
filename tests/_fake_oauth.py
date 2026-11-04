"""A stateless-enough fake MCP server + OAuth authorization server for the two-step login tests.

Served through ``httpx.MockTransport``. The authorization code it issues encodes the PKCE
challenge, client and redirect URI it was issued for, so the token endpoint can check the
verifier without remembering anything — which is what lets ``begin_login`` and
``complete_login`` run in two processes against two instances of this fake.
"""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

MCP_URL = "https://mcp.test/mcp"
AS_URL = "https://as.test"
PRM_URL = "https://mcp.test/.well-known/oauth-protected-resource/mcp"


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _unb64(text: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))


class FakeOAuth:
    def __init__(self, *, require_auth: bool = True, token_error: tuple[int, dict] | None = None, as_url: str = AS_URL):
        self.require_auth = require_auth
        self.token_error = token_error
        self.as_url = as_url
        self.registrations: list[dict] = []
        self.token_requests: list[dict] = []
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = request.url
        if url.host == "mcp.test" and url.path == "/mcp":
            if not self.require_auth:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 0, "result": {}})
            return httpx.Response(401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM_URL}"'})
        if url.host == "mcp.test" and url.path.startswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json={"resource": MCP_URL, "authorization_servers": [self.as_url]})
        as_host = urlparse(self.as_url).hostname
        if url.host == as_host and url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": self.as_url,
                    "authorization_endpoint": f"{self.as_url}/authorize",
                    "token_endpoint": f"{self.as_url}/token",
                    "registration_endpoint": f"{self.as_url}/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if url.host == as_host and url.path == "/register":
            body = json.loads(request.content)
            self.registrations.append(body)
            return httpx.Response(201, json={**body, "client_id": f"client-{len(self.registrations)}"})
        if url.host == as_host and url.path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if self.token_error is not None:
                status, body = self.token_error
                return httpx.Response(status, json=body)
            issued = _unb64(form["code"])
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if (
                challenge != issued["challenge"]
                or form["client_id"] != issued["client_id"]
                or form["redirect_uri"] != issued["redirect_uri"]
            ):
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "PKCE or client mismatch"}
                )
            return httpx.Response(
                200,
                json={
                    "access_token": f"at-{issued['client_id']}",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": f"rt-{issued['client_id']}",
                },
            )
        return httpx.Response(404)

    @staticmethod
    def authorize(authorization_url: str, *, deny: bool = False, state: str | None = None) -> str:
        """Play the user consenting (or declining) and return the redirect URL."""
        q = {k: v[0] for k, v in parse_qs(urlparse(authorization_url).query).items()}
        returned_state = q["state"] if state is None else state
        if deny:
            params = {"error": "access_denied", "error_description": "User declined", "state": returned_state}
        else:
            code = _b64(
                {"challenge": q["code_challenge"], "client_id": q["client_id"], "redirect_uri": q["redirect_uri"]}
            )
            params = {"code": code, "state": returned_state}
        return f"{q['redirect_uri']}?{urlencode(params)}"

    def client_factory(self):
        """A drop-in for ``mcpgen._login._oauth_client``."""

        @asynccontextmanager
        async def _client():
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handle)) as client:
                yield client

        return _client
