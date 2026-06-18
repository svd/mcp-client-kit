"""Manifest loader for mcp-client-kit-eval server specs."""
from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_TRANSPORTS = {"stdio", "http", "sse"}
_AUTH_PREFIX_BEARER = "bearer:"


@dataclass
class ServerSpec:
    name: str
    transport: Literal["stdio", "http", "sse"]
    launch: str  # stdio command OR URL
    auth: str    # "none" | "oauth" | "bearer:ENV_VAR"
    expected_modes: list[str] = field(default_factory=list)
    notes: str = ""
    env: dict[str, str] = field(default_factory=dict)

    # Convenience properties:
    @property
    def auth_kind(self) -> Literal["none", "oauth", "bearer"]:
        """Return the auth kind without the env-var suffix."""
        if self.auth == "none":
            return "none"
        if self.auth == "oauth":
            return "oauth"
        if self.auth.startswith(_AUTH_PREFIX_BEARER):
            return "bearer"
        raise ValueError(f"Unknown auth format: {self.auth!r}")

    @property
    def bearer_env_var(self) -> str | None:
        """Return the env-var name for bearer auth, or None."""
        if self.auth.startswith(_AUTH_PREFIX_BEARER):
            return self.auth[len(_AUTH_PREFIX_BEARER):]
        return None


def load_manifest(path: Path | str = Path("servers/servers.toml")) -> list[ServerSpec]:
    """Load all server specs from a TOML manifest."""
    path = Path(path)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    specs = []
    for entry in data.get("server", []):
        name = entry["name"]
        transport = entry["transport"]
        if transport not in _TRANSPORTS:
            raise ValueError(f"Server {name!r}: unknown transport {transport!r}")
        specs.append(ServerSpec(
            name=name,
            transport=transport,
            launch=entry["launch"],
            auth=entry.get("auth", "none"),
            expected_modes=entry.get("expected_modes", []),
            notes=entry.get("notes", ""),
            env=entry.get("env", {}),
        ))
    return specs


def get_server(name: str, path: Path | str = Path("servers/servers.toml")) -> ServerSpec:
    """Get a single server spec by name."""
    for spec in load_manifest(path):
        if spec.name == name:
            return spec
    raise KeyError(f"Server {name!r} not found in {path}")
