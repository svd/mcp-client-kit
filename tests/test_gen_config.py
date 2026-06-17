"""Tests for eval_harness.gen_config — mcpServers entry generation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval_harness.gen_config import _spec_to_entry, build_mcp_config
from eval_harness.manifest import ServerSpec


def _http_spec(auth: str = "none") -> ServerSpec:
    return ServerSpec(
        name="myserver",
        transport="http",
        launch="https://example.com/mcp",
        auth=auth,
    )


def _stdio_spec() -> ServerSpec:
    return ServerSpec(
        name="myserver",
        transport="stdio",
        launch="npx -y @foo/bar --flag",
        auth="none",
    )


# ---------------------------------------------------------------------------
# bearer HTTP → headers block
# ---------------------------------------------------------------------------

def test_bearer_http_emits_headers():
    entry = _spec_to_entry(_http_spec("bearer:GITHUB_PAT"))
    assert "headers" in entry
    assert entry["headers"] == {"Authorization": "Bearer ${GITHUB_PAT}"}


def test_bearer_http_placeholder_not_expanded(monkeypatch):
    """${VAR} must stay literal — not expanded at gen time."""
    monkeypatch.setenv("GITHUB_PAT", "secret-token-value")
    entry = _spec_to_entry(_http_spec("bearer:GITHUB_PAT"))
    assert entry["headers"]["Authorization"] == "Bearer ${GITHUB_PAT}"
    assert "secret-token-value" not in entry["headers"]["Authorization"]


def test_bearer_http_type_url_present():
    entry = _spec_to_entry(_http_spec("bearer:GITHUB_PAT"))
    assert entry["type"] == "http"
    assert entry["url"] == "https://example.com/mcp"


# ---------------------------------------------------------------------------
# no-auth HTTP → no headers
# ---------------------------------------------------------------------------

def test_none_auth_http_no_headers():
    entry = _spec_to_entry(_http_spec("none"))
    assert "headers" not in entry


# ---------------------------------------------------------------------------
# stdio → unchanged (command/args, no headers)
# ---------------------------------------------------------------------------

def test_stdio_spec_no_headers():
    entry = _spec_to_entry(_stdio_spec())
    assert "headers" not in entry
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@foo/bar", "--flag"]


# ---------------------------------------------------------------------------
# build_mcp_config integration
# ---------------------------------------------------------------------------

def test_build_mcp_config_bearer():
    specs = [_http_spec("bearer:MY_TOKEN")]
    config = build_mcp_config(specs)
    server = config["mcpServers"]["myserver"]
    assert server["headers"] == {"Authorization": "Bearer ${MY_TOKEN}"}


def test_build_mcp_config_none_no_headers():
    specs = [_http_spec("none")]
    config = build_mcp_config(specs)
    assert "headers" not in config["mcpServers"]["myserver"]
