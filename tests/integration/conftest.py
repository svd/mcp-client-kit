"""Shared fixtures for the stdio end-to-end suite.

These tests spawn a real MCP server subprocess and speak the real protocol. They
are local, deterministic and secret-free, so they belong in default CI. Remote
public endpoints deliberately do not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def stdio_cmd() -> str:
    """The --stdio command string for the unmodified fixture server."""
    return f"{sys.executable} {FIXTURES / 'stdio_server.py'}"


@pytest.fixture
def changed_stdio_cmd() -> str:
    """The --stdio command string for the mutated fixture server."""
    return f"{sys.executable} {FIXTURES / 'stdio_server_changed.py'}"
