"""MCP server discovery — enumerate servers configured in a host environment.

Provides :class:`DiscoveredServer` (plain dataclass), a :class:`HostProvider`
protocol, and :class:`ClaudeCodeProvider` (reads the Claude Code CLI or
``~/.claude.json`` as a fallback).

Usage::

    from mcpgen.discovery import discover_all
    servers = discover_all()                   # all available hosts
    servers = discover_all(hosts=["claude-code"])  # specific host

This module is purely enumerative — it does NOT connect to or probe servers.
No async. No dependency on :mod:`mcpgen._bridge`.
"""
from __future__ import annotations

import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DiscoveredServer:
    """A single MCP server entry discovered from a host environment.

    Fields are set to safe defaults; callers should treat any Optional field
    as possibly absent.
    """

    host: str
    """Provider id that surfaced this entry (e.g. ``"claude-code"``)."""

    name: str
    """Server name as registered in the host config."""

    transport: str = "unknown"
    """Transport hint: ``"stdio"``, ``"http"``, ``"sse"``, or ``"unknown"``."""

    command: str | None = None
    """Executable for stdio transport (e.g. ``"codegraph"``)."""

    args: list[str] = dataclasses.field(default_factory=list)
    """Args for stdio transport (e.g. ``["serve", "--mcp"]``)."""

    env: dict[str, str] = dataclasses.field(default_factory=dict)
    """Extra environment variables (usually empty)."""

    url: str | None = None
    """URL for http/sse transport."""

    scope: str | None = None
    """Source scope label (e.g. ``"User config (file)"``, ``"Project config (file)"``)."""

    status: str | None = None
    """Connection status string (e.g. ``"Connected"``, ``"Needs authentication"``)."""

    probeable: bool = True
    """Whether mcpgen can open a session to this server."""

    note: str | None = None
    """Human-readable annotation explaining any special handling."""

    def as_dict(self, *, redact_env: bool = True) -> dict:
        """Return a JSON-serialisable copy of this server entry.

        By default env values are redacted to ``"***"`` to avoid leaking
        credentials stored in ``~/.claude.json`` or similar host configs.
        Pass ``redact_env=False`` to include raw values.
        """
        d = dataclasses.asdict(self)
        if redact_env and d.get("env"):
            d["env"] = {k: "***" for k in d["env"]}
        return d


# ---------------------------------------------------------------------------
# HostProvider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class HostProvider(Protocol):
    """Protocol for host-environment discovery adapters."""

    id: str
    display_name: str

    def available(self) -> bool:
        """Return True if this host is present on the current machine."""
        ...

    def discover(self) -> list[DiscoveredServer]:
        """Return all servers found in this host environment."""
        ...


# ---------------------------------------------------------------------------
# Subprocess helper default
# ---------------------------------------------------------------------------

def _default_run(cmd: list[str]) -> str | None:
    """Run *cmd*, return stdout on success (rc==0), else None."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout if result.returncode == 0 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ClaudeCodeProvider
# ---------------------------------------------------------------------------

_CLAUDE_AI_CONNECTOR_NOTE = (
    "claude.ai connector — managed OAuth, not probeable by mcpgen"
)

# Match "<name>: <rest> - <status_part>" with the status being everything
# after the LAST " - " on the line.
_LIST_LINE_RE = re.compile(r"^(?P<name>.+?):\s+(?P<rest>.+)\s+-\s+(?P<status>.+)$")


def _parse_mcp_list(output: str) -> dict[str, dict]:
    """Parse ``claude mcp list`` stdout into {name: {rest, status, transport_hint}}.

    Ignores header/blank lines. Returns an empty dict if output is empty or
    cannot be parsed.
    """
    entries: dict[str, dict] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _LIST_LINE_RE.match(line)
        if m is None:
            continue
        name = m.group("name").strip()
        rest = m.group("rest").strip()
        status = m.group("status").strip()

        # Strip leading status symbols (✔ / !) — keep the text after them.
        status = re.sub(r"^[✔!]\s*", "", status)

        # Determine a coarse transport hint from the "rest" column.
        # Strip trailing "(HTTP)" annotation from rest before URL detection.
        rest_clean = re.sub(r"\s*\(HTTP\)\s*$", "", rest, flags=re.IGNORECASE).strip()
        if rest_clean.startswith(("http://", "https://")):
            transport_hint = "http"
            url_hint: str | None = rest_clean
        else:
            transport_hint = "stdio"
            url_hint = None

        entries[name] = {
            "status": status,
            "transport_hint": transport_hint,
            "url_hint": url_hint,
            "rest": rest,
        }
    return entries


def _parse_mcp_get(output: str) -> dict[str, str | None]:
    """Parse ``claude mcp get <name>`` stdout into a flat {key: value} dict.

    Recognises: ``Type``, ``Command``, ``Args``, ``URL``, ``Scope``, ``Status``.
    Unknown fields are silently ignored. Missing fields stay absent (caller
    treats them as None).
    """
    known: dict[str, str | None] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.endswith(":"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in ("Type", "Command", "Args", "URL", "Scope", "Status"):
            known[key] = value
    return known


def _is_claude_ai_connector(name: str, scope: str | None) -> bool:
    """Return True if this entry is a claude.ai-managed connector."""
    if name.startswith("claude.ai "):
        return True
    if scope and "claude.ai" in scope:
        return True
    return False


def _build_server_from_get(
    host: str,
    name: str,
    list_entry: dict,
    get_fields: dict[str, str | None],
) -> DiscoveredServer:
    """Construct a :class:`DiscoveredServer` from the combined list+get info."""
    raw_type = (get_fields.get("Type") or list_entry.get("transport_hint") or "unknown").lower()
    if raw_type in ("http", "sse"):
        transport = raw_type
    elif raw_type == "stdio":
        transport = "stdio"
    else:
        transport = "unknown"

    # Status: prefer the more detailed ``get`` output.
    raw_status = get_fields.get("Status") or list_entry.get("status")
    if raw_status:
        raw_status = re.sub(r"^[✔!]\s*", "", raw_status).strip() or raw_status.strip()

    scope = get_fields.get("Scope")
    url = get_fields.get("URL") or list_entry.get("url_hint")
    command: str | None = get_fields.get("Command")
    raw_args = get_fields.get("Args")
    args = raw_args.split() if raw_args else []

    is_connector = _is_claude_ai_connector(name, scope)
    return DiscoveredServer(
        host=host,
        name=name,
        transport=transport,
        command=command if transport == "stdio" else None,
        args=args if transport == "stdio" else [],
        url=url if transport in ("http", "sse") else None,
        scope=scope,
        status=raw_status,
        probeable=not is_connector,
        note=_CLAUDE_AI_CONNECTOR_NOTE if is_connector else None,
    )


class ClaudeCodeProvider:
    """Discover MCP servers from the Claude Code CLI or ``~/.claude.json``.

    Injectable dependencies allow deterministic unit testing without touching
    the real filesystem or spawning real processes.

    Parameters
    ----------
    _run:
        Callable that accepts a command list and returns stdout (str) or None
        on non-zero exit / error. Default: real subprocess runner.
    _home:
        Home directory path. Default: ``Path.home()``.
    """

    id = "claude-code"
    display_name = "Claude Code"

    def __init__(
        self,
        *,
        _run: Callable[[list[str]], str | None] = _default_run,
        _home: Path | None = None,
    ) -> None:
        self._run = _run
        self._home = _home if _home is not None else Path.home()

    # ------------------------------------------------------------------
    # HostProvider interface
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """Return True if the Claude Code CLI or its config file is present."""
        return (
            shutil.which("claude") is not None
            or (self._home / ".claude.json").exists()
        )

    def discover(self) -> list[DiscoveredServer]:
        """Return all MCP servers found in the Claude Code environment.

        Reads ``~/.claude.json`` for user- and project-scoped servers.
        The ``claude mcp list`` CLI is intentionally skipped here: it runs live
        health-checks that block when called from a subprocess without a tty.
        The CLI parsing helpers (``_parse_mcp_list``, ``_parse_mcp_get``) are
        kept for potential future use and are covered by unit tests.
        """
        return self._discover_via_json()

    # ------------------------------------------------------------------
    # CLI path
    # ------------------------------------------------------------------

    def _discover_via_cli(self) -> list[DiscoveredServer] | None:
        """Return servers from ``claude mcp list/get`` or None on failure.

        WARNING: ``claude mcp list`` runs live health-checks and blocks
        indefinitely when invoked from a subprocess without a tty. ``discover()``
        deliberately does NOT call this — it uses the JSON path. Do not re-wire
        ``discover()`` to call this without a tty guard or a hard subprocess
        timeout, or non-interactive callers (CI, daemons) will hang.
        """
        list_output = self._run(["claude", "mcp", "list"])
        if list_output is None:
            return None

        list_entries = _parse_mcp_list(list_output)
        if not list_entries:
            # Could be an empty config — return empty list, not None.
            return []

        servers: list[DiscoveredServer] = []
        for name, list_entry in list_entries.items():
            get_output = self._run(["claude", "mcp", "get", name])
            get_fields: dict[str, str | None] = (
                _parse_mcp_get(get_output) if get_output is not None else {}
            )
            servers.append(
                _build_server_from_get(self.id, name, list_entry, get_fields)
            )
        return servers

    # ------------------------------------------------------------------
    # JSON fallback
    # ------------------------------------------------------------------

    def _discover_via_json(self) -> list[DiscoveredServer]:
        """Return servers parsed from ``~/.claude.json``."""
        config_path = self._home / ".claude.json"
        if not config_path.exists():
            return []

        try:
            raw = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        if not isinstance(raw, dict):
            return []

        # Top-level mcpServers — user scope (higher precedence). Guard every
        # level against non-dict values: a malformed config (e.g. an array or
        # scalar where a mapping is expected) must not crash discovery.
        raw_user = raw.get("mcpServers")
        user_servers: dict[str, dict] = raw_user if isinstance(raw_user, dict) else {}

        # Project-scope: keyed by absolute cwd path.
        cwd_key = str(Path.cwd())
        raw_projects = raw.get("projects")
        projects = raw_projects if isinstance(raw_projects, dict) else {}
        project_block = projects.get(cwd_key)
        if not isinstance(project_block, dict):
            project_block = {}
        raw_project = project_block.get("mcpServers")
        project_servers: dict[str, dict] = raw_project if isinstance(raw_project, dict) else {}

        # Merge: user scope wins on name conflicts.
        merged: dict[str, tuple[dict, str]] = {}  # name -> (entry, scope_label)
        for name, entry in project_servers.items():
            if isinstance(entry, dict):
                merged[name] = (entry, "Project config (file)")
        for name, entry in user_servers.items():
            if isinstance(entry, dict):
                merged[name] = (entry, "User config (file)")

        servers: list[DiscoveredServer] = []
        for name, (entry, scope_label) in merged.items():
            server = self._entry_to_server(name, entry, scope_label)
            servers.append(server)
        return servers

    def _entry_to_server(
        self, name: str, entry: dict, scope: str
    ) -> DiscoveredServer:
        """Convert a single mcpServers JSON entry to a :class:`DiscoveredServer`."""
        # Determine transport from the presence of url/command keys or a "type" hint.
        explicit_type = (entry.get("type") or "").lower()
        url: str | None = entry.get("url")
        command: str | None = entry.get("command")

        if url:
            transport: str = explicit_type if explicit_type in ("http", "sse") else "http"
        elif command:
            transport = "stdio"
        else:
            transport = "unknown"

        raw_args = entry.get("args") or []
        if isinstance(raw_args, list):
            args = [str(a) for a in raw_args]
        else:
            args = []

        raw_env = entry.get("env") or {}
        if not isinstance(raw_env, dict):
            raw_env = {}
        env = {str(k): str(v) for k, v in raw_env.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}

        is_connector = _is_claude_ai_connector(name, scope)
        return DiscoveredServer(
            host=self.id,
            name=name,
            transport=transport,
            command=command if transport == "stdio" else None,
            args=args if transport == "stdio" else [],
            env=env,
            url=url if transport in ("http", "sse") else None,
            scope=scope,
            status=None,
            probeable=not is_connector,
            note=_CLAUDE_AI_CONNECTOR_NOTE if is_connector else None,
        )


# ---------------------------------------------------------------------------
# Provider registry and module-level discover_all
# ---------------------------------------------------------------------------

PROVIDERS: list[HostProvider] = [ClaudeCodeProvider()]
"""Registry of all known host providers. Extend to add new hosts."""


def discover_all(hosts: list[str] | None = None) -> list[DiscoveredServer]:
    """Discover MCP servers across all (or selected) host providers.

    Parameters
    ----------
    hosts:
        Optional allowlist of provider :attr:`HostProvider.id` values. When
        given, only matching providers are queried. Pass ``None`` (default) to
        query all providers.

    Returns
    -------
    list[DiscoveredServer]
        Results from all qualifying, available providers, in provider order.
    """
    results: list[DiscoveredServer] = []
    for provider in PROVIDERS:
        if hosts is not None and provider.id not in hosts:
            continue
        if not provider.available():
            continue
        results.extend(provider.discover())
    return results
