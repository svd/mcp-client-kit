"""Pure unit tests for mcp_client_kit.discovery — no network, no subprocess."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from mcp_client_kit.discovery import (
    ClaudeCodeProvider,
    DiscoveredServer,
    _parse_mcp_get,
    _parse_mcp_list,
    discover_all,
)

# ---------------------------------------------------------------------------
# Fixtures (inline strings)
# ---------------------------------------------------------------------------

FIXTURE_MCP_LIST = """\
Checking MCP server health…

claude.ai Microsoft 365: https://microsoft365.mcp.claude.com/mcp - ✔ Connected
claude.ai Context7: https://mcp.context7.com/mcp - ✔ Connected
codegraph: codegraph serve --mcp - ✔ Connected
m365-copilot: node /Users/user/src/m365copilot-mcp/build/index.js - ✔ Connected
epam-radar: https://mcp.epam.com/mcp/radar (HTTP) - ! Needs authentication
epam-staffing: https://mcp.epam.com/mcp/staffing (HTTP) - ! Needs authentication
"""

FIXTURE_GET_CODEGRAPH = """\
codegraph:
  Scope: User config (available in all your projects)
  Status: ✔ Connected
  Type: stdio
  Command: codegraph
  Args: serve --mcp

To remove this server, run: claude mcp remove "codegraph" -s user
"""

FIXTURE_GET_EPAM_RADAR = """\
epam-radar:
  Scope: User config (available in all your projects)
  Status: ! Needs authentication
  Type: http
  URL: https://mcp.epam.com/mcp/radar
"""

FIXTURE_GET_CONTEXT7 = """\
claude.ai Context7:
  Scope: User config (available in all your projects)
  Status: ✔ Connected
  Type: http
  URL: https://mcp.context7.com/mcp
"""

FIXTURE_CLAUDE_JSON = {
    "mcpServers": {
        "codegraph": {
            "type": "stdio",
            "command": "codegraph",
            "args": ["serve", "--mcp"],
        },
        "epam-radar": {
            "type": "http",
            "url": "https://mcp.epam.com/mcp/radar",
        },
    },
    "projects": {
        "/Users/user/myproject": {
            "mcpServers": {
                "local-tool": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "my_tool"],
                },
                "epam-radar": {
                    "type": "http",
                    "url": "https://old.epam.com/mcp/radar",
                },
            }
        }
    },
}


# ---------------------------------------------------------------------------
# Helper: build a _run callable backed by a cmd→output mapping
# ---------------------------------------------------------------------------

def _make_run(mapping: dict[tuple, str | None]):
    """Return a _run callable that maps command tuples to output strings."""

    def _run(cmd: list[str]) -> str | None:
        return mapping.get(tuple(cmd))

    return _run


# ---------------------------------------------------------------------------
# Tests: _parse_mcp_list
# ---------------------------------------------------------------------------


def test_parse_mcp_list_stdio_server():
    entries = _parse_mcp_list(FIXTURE_MCP_LIST)
    assert "codegraph" in entries
    entry = entries["codegraph"]
    assert entry["transport_hint"] == "stdio"
    assert entry["url_hint"] is None
    assert entry["status"] == "Connected"


def test_parse_mcp_list_http_server():
    entries = _parse_mcp_list(FIXTURE_MCP_LIST)
    assert "epam-radar" in entries
    entry = entries["epam-radar"]
    assert entry["transport_hint"] == "http"
    assert entry["url_hint"] == "https://mcp.epam.com/mcp/radar"
    assert "(HTTP)" not in (entry["url_hint"] or "")
    assert entry["status"] == "Needs authentication"


def test_parse_mcp_list_connector():
    entries = _parse_mcp_list(FIXTURE_MCP_LIST)
    assert "claude.ai Context7" in entries
    entry = entries["claude.ai Context7"]
    assert entry["transport_hint"] == "http"
    assert entry["url_hint"] == "https://mcp.context7.com/mcp"


# ---------------------------------------------------------------------------
# Tests: _parse_mcp_get
# ---------------------------------------------------------------------------


def test_parse_mcp_get_stdio():
    fields = _parse_mcp_get(FIXTURE_GET_CODEGRAPH)
    assert fields.get("Type") == "stdio"
    assert fields.get("Command") == "codegraph"
    assert fields.get("Args") == "serve --mcp"
    assert (fields.get("Scope") or "").startswith("User config")


def test_parse_mcp_get_http():
    fields = _parse_mcp_get(FIXTURE_GET_EPAM_RADAR)
    assert fields.get("Type") == "http"
    assert fields.get("URL") == "https://mcp.epam.com/mcp/radar"


# ---------------------------------------------------------------------------
# Tests: CLI discovery path
# ---------------------------------------------------------------------------


def test_cli_discover_codegraph(tmp_path):
    run_map = {
        ("claude", "mcp", "list"): FIXTURE_MCP_LIST,
        ("claude", "mcp", "get", "codegraph"): FIXTURE_GET_CODEGRAPH,
        ("claude", "mcp", "get", "claude.ai Microsoft 365"): None,
        ("claude", "mcp", "get", "claude.ai Context7"): FIXTURE_GET_CONTEXT7,
        ("claude", "mcp", "get", "m365-copilot"): None,
        ("claude", "mcp", "get", "epam-radar"): FIXTURE_GET_EPAM_RADAR,
        ("claude", "mcp", "get", "epam-staffing"): None,
    }

    # Call _discover_via_cli() directly — discover() uses JSON path by default
    # (claude mcp list hangs in subprocess due to live health-checks without a tty).
    provider = ClaudeCodeProvider(_run=_make_run(run_map), _home=tmp_path)
    servers = provider._discover_via_cli()

    by_name = {s.name: s for s in servers}
    assert "codegraph" in by_name
    cg = by_name["codegraph"]
    assert cg.transport == "stdio"
    assert cg.command == "codegraph"
    assert cg.args == ["serve", "--mcp"]
    assert cg.probeable is True
    assert cg.note is None


def test_cli_connector_not_probeable(tmp_path):
    run_map = {
        ("claude", "mcp", "list"): FIXTURE_MCP_LIST,
        ("claude", "mcp", "get", "codegraph"): FIXTURE_GET_CODEGRAPH,
        ("claude", "mcp", "get", "claude.ai Microsoft 365"): None,
        ("claude", "mcp", "get", "claude.ai Context7"): FIXTURE_GET_CONTEXT7,
        ("claude", "mcp", "get", "m365-copilot"): None,
        ("claude", "mcp", "get", "epam-radar"): FIXTURE_GET_EPAM_RADAR,
        ("claude", "mcp", "get", "epam-staffing"): None,
    }

    # Call _discover_via_cli() directly — discover() uses JSON path by default.
    provider = ClaudeCodeProvider(_run=_make_run(run_map), _home=tmp_path)
    servers = provider._discover_via_cli()

    by_name = {s.name: s for s in servers}
    assert "claude.ai Context7" in by_name
    ctx7 = by_name["claude.ai Context7"]
    assert ctx7.probeable is False
    assert ctx7.note is not None
    assert "claude.ai connector" in ctx7.note


# ---------------------------------------------------------------------------
# Tests: JSON fallback path
# ---------------------------------------------------------------------------


def test_cli_json_fallback(tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps(FIXTURE_CLAUDE_JSON))

    # discover() always reads ~/.claude.json; _run is not called.
    provider = ClaudeCodeProvider(_run=_make_run({}), _home=tmp_path)
    servers = provider.discover()

    by_name = {s.name: s for s in servers}

    # codegraph should be present with stdio transport.
    assert "codegraph" in by_name
    cg = by_name["codegraph"]
    assert cg.transport == "stdio"
    assert cg.command == "codegraph"
    assert cg.args == ["serve", "--mcp"]

    # epam-radar: user scope URL wins over project scope URL.
    assert "epam-radar" in by_name
    er = by_name["epam-radar"]
    assert er.transport == "http"
    assert er.url == "https://mcp.epam.com/mcp/radar"

    # local-tool is only in a project scope that doesn't match cwd (tmp_path).
    assert "local-tool" not in by_name


def test_json_malformed_mcpservers_does_not_crash(tmp_path):
    # Non-dict where a mapping is expected at every level must yield [] —
    # truthy non-dict values previously slipped past `or {}` and crashed on
    # .items().
    bad = {
        "mcpServers": ["not", "a", "dict"],
        "projects": "also-not-a-dict",
    }
    (tmp_path / ".claude.json").write_text(json.dumps(bad))
    provider = ClaudeCodeProvider(_run=_make_run({}), _home=tmp_path)
    assert provider.discover() == []


def test_json_non_object_root_does_not_crash(tmp_path):
    # A JSON array at the root (valid JSON, wrong shape) must not crash.
    (tmp_path / ".claude.json").write_text("[1, 2, 3]")
    provider = ClaudeCodeProvider(_run=_make_run({}), _home=tmp_path)
    assert provider.discover() == []


# ---------------------------------------------------------------------------
# Tests: available()
# ---------------------------------------------------------------------------


def test_available_with_json_only(tmp_path):
    (tmp_path / ".claude.json").write_text("{}")
    with patch("mcp_client_kit.discovery.shutil.which", return_value=None):
        provider = ClaudeCodeProvider(_home=tmp_path)
        assert provider.available() is True


def test_available_false_when_nothing(tmp_path):
    # No .claude.json in tmp_path, no claude on PATH.
    with patch("mcp_client_kit.discovery.shutil.which", return_value=None):
        provider = ClaudeCodeProvider(_home=tmp_path)
        assert provider.available() is False


# ---------------------------------------------------------------------------
# Tests: discover_all()
# ---------------------------------------------------------------------------


def test_discover_all_host_filter():
    result = discover_all(hosts=["nonexistent-host"])
    assert result == []


def test_discover_all_skips_unavailable(tmp_path, monkeypatch):
    from mcp_client_kit import discovery

    # Replace the singleton provider with one whose available() returns False.
    fake_provider = ClaudeCodeProvider(_home=tmp_path)
    monkeypatch.setattr(discovery, "PROVIDERS", [fake_provider])
    with patch("mcp_client_kit.discovery.shutil.which", return_value=None):
        # No .claude.json → available() returns False.
        result = discover_all()
    assert result == []
