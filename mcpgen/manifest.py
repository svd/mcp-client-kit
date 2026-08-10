"""Canonical tool-inventory snapshots for drift detection.

Pure functions only — no I/O, no network, no clock. A manifest is a
deterministic function of ``(server, tools)`` so that regenerating an unchanged
inventory produces byte-identical output and never dirties a git diff. That is
why no timestamp and no generator version are recorded here; ``FORMAT_VERSION``
alone carries format migration.

The unit consumed and produced is the tool dict emitted by
``cli._list_tools()``: ``{"name", "description", "inputSchema", "annotations"}``.
"""

from __future__ import annotations

import json
from typing import Any

FORMAT_VERSION = 1

# JSON Schema keywords whose array value is a set, not a sequence. Reordering
# them is not a semantic change, so they are sorted before comparison — this is
# what stops a server that re-emits `required` in a different order from
# reading as drift.
_SET_LIKE_KEYS = frozenset({"required", "enum"})


def _sort_key(value: Any) -> str:
    """Total order over heterogeneous JSON scalars: compare by canonical JSON text."""
    return json.dumps(value, sort_keys=True, default=str)


def canonical(obj: Any) -> Any:
    """Return *obj* with object keys sorted recursively and set-like arrays sorted.

    Positional arrays (``prefixItems``, ``allOf``, ``anyOf`` …) keep their order:
    reordering them *is* a semantic change and must surface as drift.
    """
    if isinstance(obj, dict):
        return {k: _canonical_value(k, obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [canonical(item) for item in obj]
    return obj


def _canonical_value(key: str, value: Any) -> Any:
    if key in _SET_LIKE_KEYS and isinstance(value, list):
        return sorted((canonical(item) for item in value), key=_sort_key)
    return canonical(value)


def build(server: str, tools: list[dict]) -> dict:
    """Build a canonical manifest from a ``tools/list`` inventory."""
    entries: dict[str, dict] = {}
    for tool in tools:
        entries[tool["name"]] = {
            "description": tool.get("description") or "",
            "inputSchema": canonical(tool.get("inputSchema") or {}),
            "annotations": canonical(tool.get("annotations")) if tool.get("annotations") is not None else None,
        }
    return {
        "format_version": FORMAT_VERSION,
        "server": server,
        "tools": {name: entries[name] for name in sorted(entries)},
    }


def dumps(obj: dict) -> str:
    """Serialize a manifest to stable, newline-terminated JSON text."""
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"
