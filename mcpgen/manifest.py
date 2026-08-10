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
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ToolChange:
    """One difference for one tool.

    category: 'required' | 'enum' | 'input_schema' | 'annotations' | 'description'
    detail:   short human-readable summary, e.g. "required properties changed: +precision"
    old/new:  the compared fragments, for --json consumers and verbose display
    """

    tool: str
    category: str
    detail: str
    old: Any = None
    new: Any = None


@dataclass(frozen=True)
class DriftReport:
    server: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[ToolChange] = field(default_factory=list)
    advisory: list[ToolChange] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        """True when a tool was added, removed, or had its contract change.

        Advisories (description-only differences) deliberately do not count.
        """
        return bool(self.added or self.removed or self.changed)


def _validate(m: dict, label: str) -> None:
    version = m.get("format_version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"{label} manifest has format_version {version!r}, expected {FORMAT_VERSION}. "
            f"Regenerate it with `mcpgen codegen`."
        )
    if not isinstance(m.get("tools"), dict):
        raise ValueError(f"{label} manifest has no 'tools' object")


def _collect_enums(schema: Any, path: str = "") -> dict[str, list]:
    """Map dotted property path → enum list, for pinpointing which field's enum moved."""
    found: dict[str, list] = {}
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "enum" and isinstance(value, list):
                found[path or "<root>"] = value
            elif key == "properties" and isinstance(value, dict):
                for prop, sub in value.items():
                    found.update(_collect_enums(sub, f"{path}.{prop}" if path else prop))
            else:
                found.update(_collect_enums(value, path))
    elif isinstance(schema, list):
        for item in schema:
            found.update(_collect_enums(item, path))
    return found


def _classify(tool: str, old: dict, new: dict) -> ToolChange | None:
    """Return the most specific contract change for one tool, or None if identical."""
    old_schema, new_schema = old.get("inputSchema") or {}, new.get("inputSchema") or {}

    if old_schema != new_schema:
        old_req = set(old_schema.get("required") or [])
        new_req = set(new_schema.get("required") or [])
        if old_req != new_req:
            bits = [f"+{n}" for n in sorted(new_req - old_req)] + [f"-{n}" for n in sorted(old_req - new_req)]
            return ToolChange(
                tool, "required", f"required properties changed: {' '.join(bits)}", sorted(old_req), sorted(new_req)
            )

        old_enums, new_enums = _collect_enums(old_schema), _collect_enums(new_schema)
        moved = [p for p in sorted(set(old_enums) | set(new_enums)) if old_enums.get(p) != new_enums.get(p)]
        if moved:
            path = moved[0]
            before, after = old_enums.get(path, []), new_enums.get(path, [])
            bits = [f"+{v}" for v in after if v not in before] + [f"-{v}" for v in before if v not in after]
            return ToolChange(tool, "enum", f"enum changed for {path!r}: {' '.join(bits)}", before, after)

        return ToolChange(tool, "input_schema", "input schema changed", old_schema, new_schema)

    if old.get("annotations") != new.get("annotations"):
        return ToolChange(tool, "annotations", "annotations changed", old.get("annotations"), new.get("annotations"))
    return None


def diff(old: dict, new: dict) -> DriftReport:
    """Compare two manifests. Raises ValueError on an unusable manifest."""
    _validate(old, "stored")
    _validate(new, "live")

    old_tools, new_tools = old["tools"], new["tools"]
    added = sorted(set(new_tools) - set(old_tools))
    removed = sorted(set(old_tools) - set(new_tools))

    changed: list[ToolChange] = []
    advisory: list[ToolChange] = []
    for name in sorted(set(old_tools) & set(new_tools)):
        before, after = old_tools[name], new_tools[name]
        change = _classify(name, before, after)
        if change is not None:
            changed.append(change)
            continue
        # Only a description-only difference reaches here, so an advisory can
        # never mask a contract change for the same tool.
        if before.get("description") != after.get("description"):
            advisory.append(
                ToolChange(
                    name, "description", "description changed", before.get("description"), after.get("description")
                )
            )

    return DriftReport(
        server=new.get("server", old.get("server", "")),
        added=added,
        removed=removed,
        changed=changed,
        advisory=advisory,
    )


def render_text(report: DriftReport) -> str:
    """Human-readable drift report. Advisories print but never imply drift."""
    lines: list[str] = []
    for name in report.added:
        lines.append(f"ADDED     {report.server}.{name}")
    for name in report.removed:
        lines.append(f"REMOVED   {report.server}.{name}")
    for change in report.changed:
        lines.append(f"CHANGED   {report.server}.{change.tool}: {change.detail}")
    for change in report.advisory:
        lines.append(f"ADVISORY  {report.server}.{change.tool}: {change.detail}")
        lines.append(f"            - {change.old!r}")
        lines.append(f"            + {change.new!r}")

    suffix = f" ({len(report.advisory)} advisory)" if report.advisory else ""
    if report.has_drift:
        summary = (
            f"Drift detected: {len(report.added)} added, "
            f"{len(report.removed)} removed, {len(report.changed)} changed{suffix}."
        )
    else:
        summary = f"No drift.{suffix}"

    if lines:
        lines.append("")
    lines.append(summary)
    return "\n".join(lines)


def _change_to_json(change: ToolChange) -> dict:
    return {
        "tool": change.tool,
        "category": change.category,
        "detail": change.detail,
        "old": change.old,
        "new": change.new,
    }


def to_json(report: DriftReport) -> dict:
    """Structured drift report for CI consumers."""
    return {
        "server": report.server,
        "drift": report.has_drift,
        "added": list(report.added),
        "removed": list(report.removed),
        "changed": [_change_to_json(c) for c in report.changed],
        "advisory": [_change_to_json(c) for c in report.advisory],
    }
