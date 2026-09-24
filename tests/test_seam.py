"""The seam Protocols generated wrappers depend on."""

from __future__ import annotations

import inspect
from typing import Any

import mcpgen
from mcpgen.seam import McpCaller, McpResourceReader


class _Reader:
    async def read_resource(self, server: str, uri: str) -> Any:
        return None


class _Caller:
    async def call(self, server: str, tool: str, arguments: dict) -> Any:
        return None


def test_resource_reader_is_runtime_checkable():
    assert isinstance(_Reader(), McpResourceReader)
    assert not isinstance(_Caller(), McpResourceReader)


def test_resource_reader_signature():
    sig = inspect.signature(McpResourceReader.read_resource)
    assert list(sig.parameters) == ["self", "server", "uri"]
    assert inspect.iscoroutinefunction(McpResourceReader.read_resource)


def test_mcp_caller_signature_unchanged():
    sig = inspect.signature(McpCaller.call)
    assert list(sig.parameters) == ["self", "server", "tool", "arguments"]
    assert inspect.iscoroutinefunction(McpCaller.call)
    assert isinstance(_Caller(), McpCaller)
    assert not isinstance(_Reader(), McpCaller)


def test_resource_reader_exported():
    assert mcpgen.McpResourceReader is McpResourceReader
    assert "McpResourceReader" in mcpgen.__all__
