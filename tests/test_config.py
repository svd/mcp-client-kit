"""Tests for the server-config layer: url + clientName parsing and resolution."""
import json

from mcp_client_kit import _bridge


def _write(tmp_path, raw: dict):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(raw))
    return str(p)


def test_parse_servers_client_name_canonical():
    urls, names = _bridge._parse_servers(
        {"mcpServers": {"s": {"url": "https://x/mcp", "clientName": "My App"}}}
    )
    assert urls == {"s": "https://x/mcp"}
    assert names == {"s": "My App"}


def test_parse_servers_client_name_snake_alias():
    _, names = _bridge._parse_servers(
        {"mcpServers": {"s": {"url": "https://x/mcp", "client_name": "Snake App"}}}
    )
    assert names == {"s": "Snake App"}


def test_parse_servers_no_override_absent():
    urls, names = _bridge._parse_servers({"mcpServers": {"s": {"url": "https://x/mcp"}}})
    assert urls == {"s": "https://x/mcp"}
    assert names == {}


def test_parse_servers_flat_string_form():
    urls, names = _bridge._parse_servers({"s": "https://x/mcp"})
    assert urls == {"s": "https://x/mcp"}
    assert names == {}


def test_servers_config_path_loads_file(tmp_path):
    cfg = _write(tmp_path, {"mcpServers": {
        "custom": {"url": "https://x/mcp", "clientName": "Example Client"},
        "plain": {"url": "https://z/mcp"},
    }})
    assert _bridge.servers(config_path=cfg) == {
        "custom": "https://x/mcp",
        "plain": "https://z/mcp",
    }


def test_resolve_client_name_override_and_default(tmp_path):
    cfg = _write(tmp_path, {"mcpServers": {
        "custom": {"url": "https://x/mcp", "clientName": "Example Client"},
        "plain": {"url": "https://z/mcp"},
    }})
    _bridge.servers(config_path=cfg)  # populates the client-name cache
    assert _bridge._resolve_client_name("custom") == "Example Client"
    assert _bridge._resolve_client_name("plain") == "mcp-client-kit (plain)"
    # Unknown server falls back to the default template too.
    assert _bridge._resolve_client_name("nope") == "mcp-client-kit (nope)"


def test_servers_config_path_is_authoritative(tmp_path):
    # A config_path with no entries yields an empty registry (no search-order fallback).
    cfg = _write(tmp_path, {"mcpServers": {}})
    assert _bridge.servers(config_path=cfg) == {}
