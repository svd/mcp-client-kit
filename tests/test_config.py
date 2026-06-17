"""Tests for the server-config layer: url + clientName parsing and resolution."""
import json

from mcpgen import _bridge


def _write(tmp_path, raw: dict):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(raw))
    return str(p)


def test_parse_servers_client_name_canonical():
    urls, names, cmds, hdrs = _bridge._parse_servers(
        {"mcpServers": {"s": {"url": "https://x/mcp", "clientName": "My App"}}}
    )
    assert urls == {"s": "https://x/mcp"}
    assert names == {"s": "My App"}
    assert cmds == {}


def test_parse_servers_client_name_snake_alias():
    _, names, _, _ = _bridge._parse_servers(
        {"mcpServers": {"s": {"url": "https://x/mcp", "client_name": "Snake App"}}}
    )
    assert names == {"s": "Snake App"}


def test_parse_servers_no_override_absent():
    urls, names, cmds, hdrs = _bridge._parse_servers({"mcpServers": {"s": {"url": "https://x/mcp"}}})
    assert urls == {"s": "https://x/mcp"}
    assert names == {}
    assert cmds == {}


def test_parse_servers_flat_string_form():
    urls, names, cmds, hdrs = _bridge._parse_servers({"s": "https://x/mcp"})
    assert urls == {"s": "https://x/mcp"}
    assert names == {}
    assert cmds == {}


def test_parse_servers_stdio_entry():
    """Stdio entry (command/args, no url) lands in cmds, not urls."""
    urls, names, cmds, hdrs = _bridge._parse_servers(
        {"mcpServers": {"context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]}}}
    )
    assert "context7" not in urls
    assert cmds["context7"] == {
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
        "env": None,
    }


def test_parse_servers_stdio_env_expansion(monkeypatch):
    """env values with ${VAR} references are expanded against the host environment."""
    monkeypatch.setenv("TEST_ACCESS_TOK", "secret-abc")
    _, _, cmds, _ = _bridge._parse_servers(
        {"mcpServers": {"srv": {
            "command": "uvx",
            "args": ["my-mcp"],
            "env": {"ACCESS_TOKEN": "${TEST_ACCESS_TOK}"},
        }}}
    )
    assert cmds["srv"]["env"] == {"ACCESS_TOKEN": "secret-abc"}


def test_parse_servers_mixed_http_and_stdio():
    """Config with both http and stdio entries — each lands in the right dict."""
    urls, _, cmds, _ = _bridge._parse_servers({"mcpServers": {
        "deepwiki": {"url": "https://mcp.deepwiki.com/mcp"},
        "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
    }})
    assert urls == {"deepwiki": "https://mcp.deepwiki.com/mcp"}
    assert "deepwiki" not in cmds
    assert cmds["context7"]["command"] == "npx"
    assert "context7" not in urls


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
    assert _bridge._resolve_client_name("plain") == "mcpgen (plain)"
    # Unknown server falls back to the default template too.
    assert _bridge._resolve_client_name("nope") == "mcpgen (nope)"


def test_servers_config_path_is_authoritative(tmp_path):
    # A config_path with no entries yields an empty registry (no search-order fallback).
    cfg = _write(tmp_path, {"mcpServers": {}})
    assert _bridge.servers(config_path=cfg) == {}


def test_servers_missing_config_path_raises(tmp_path):
    """Explicit config_path that does not exist raises FileNotFoundError (Defect A fix)."""
    import pytest
    missing = str(tmp_path / "does-not-exist.json")
    with pytest.raises(FileNotFoundError, match="config not found"):
        _bridge.servers(config_path=missing)


def test_servers_unparseable_config_path_raises(tmp_path):
    """Explicit config_path with invalid JSON raises ValueError naming the path."""
    import pytest
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(ValueError, match="failed to parse config"):
        _bridge.servers(config_path=str(bad))


def test_parse_servers_headers_expansion(monkeypatch):
    """headers block in dict-with-url entry is captured and ${VAR} refs expanded."""
    monkeypatch.setenv("TEST_PAT", "ghp_abc123")
    _, _, _, hdrs = _bridge._parse_servers(
        {"mcpServers": {"github": {
            "url": "https://api.github.com/mcp",
            "headers": {"Authorization": "Bearer ${TEST_PAT}"},
        }}}
    )
    assert hdrs == {"github": {"Authorization": "Bearer ghp_abc123"}}


def test_parse_servers_no_headers_absent():
    """HTTP entry without headers yields empty hdrs dict."""
    _, _, _, hdrs = _bridge._parse_servers(
        {"mcpServers": {"plain": {"url": "https://x/mcp"}}}
    )
    assert hdrs == {}


def test_parse_servers_empty_header_key_dropped():
    """Headers entry with empty string key is silently dropped (RFC 9110)."""
    _, _, _, hdrs = _bridge._parse_servers(
        {"mcpServers": {"srv": {
            "url": "https://x/mcp",
            "headers": {"": "should-drop", "X-Valid": "keep"},
        }}}
    )
    assert hdrs == {"srv": {"X-Valid": "keep"}}


def test_filter_str_dict_expands_vars(monkeypatch):
    """_filter_str_dict expands ${VAR} and filters non-scalar values."""
    monkeypatch.setenv("MY_VAR", "hello")
    result = _bridge._filter_str_dict({"k": "${MY_VAR}", "bad": None, "n": 42})
    assert result == {"k": "hello", "n": "42"}
