"""Canonical tool- and resource-inventory snapshots for drift detection.

Pure functions only — no I/O, no network, no clock. A manifest is a
deterministic function of ``(server, tools, resources, resource_templates)`` so
that regenerating an unchanged inventory produces byte-identical output and
never dirties a git diff. That is why no timestamp and no generator version are
recorded here; ``FORMAT_VERSION`` alone carries format migration.

The unit consumed for tools is the tool dict emitted by ``cli._list_tools()``:
``{"name", "description", "inputSchema", "annotations"}``. Resources and
resource templates are MCP ``Resource`` / ``ResourceTemplate`` dicts, keyed by
``uri`` (already normalized by the caller) and ``uriTemplate`` respectively.

The ``resources`` and ``resource_templates`` sections are optional within
format version 1. An absent section means the inventory was not collected and
the section is untracked; a present one, even ``{}``, is tracked. Older mcpgen
versions ignore the extra keys.
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


# (manifest section, report kind)
_RESOURCE_SECTIONS = (
    ("resources", "resource"),
    ("resource_templates", "resource_template"),
)


def _in_key_order(items: list[dict], key_field: str) -> list[dict]:
    """Order entries by key, then by canonical JSON text.

    When two entries share a key the later one wins, so this ordering makes the
    winner independent of the order the server listed them in.
    """
    return sorted(items, key=lambda item: (str(item[key_field]), _sort_key(item)))


def _resource_section(items: list[dict], key_field: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for item in _in_key_order(items, key_field):
        entries[str(item[key_field])] = {
            "name": item.get("name") or "",
            "description": item.get("description") or "",
            "mimeType": item.get("mimeType"),
        }
    return entries


def build(
    server: str,
    tools: list[dict],
    resources: list[dict] | None = None,
    resource_templates: list[dict] | None = None,
) -> dict:
    """Build a canonical manifest from ``tools/list`` and, when collected, the resource listings.

    ``resources`` / ``resource_templates`` of ``None`` mean "not collected" and
    leave the section out; an empty list writes an empty, tracked section.
    """
    entries: dict[str, dict] = {}
    for tool in _in_key_order(tools, "name"):
        entries[tool["name"]] = {
            "description": tool.get("description") or "",
            "inputSchema": canonical(tool.get("inputSchema") or {}),
            "annotations": canonical(tool.get("annotations")) if tool.get("annotations") is not None else None,
        }
    manifest: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "server": server,
        "tools": entries,
    }
    if resources is not None:
        manifest["resources"] = _resource_section(resources, "uri")
    if resource_templates is not None:
        manifest["resource_templates"] = _resource_section(resource_templates, "uriTemplate")
    return manifest


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
class ResourceRef:
    """A resource or resource template that was added or removed.

    kind: 'resource' | 'resource_template'
    key:  the normalized URI, or the uriTemplate string
    """

    kind: str
    key: str


@dataclass(frozen=True)
class ResourceChange:
    """One difference for one resource or resource template.

    category: 'mimeType' (drift) | 'name' | 'description' (advisory)
    """

    kind: str
    key: str
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
    resources_added: list[ResourceRef] = field(default_factory=list)
    resources_removed: list[ResourceRef] = field(default_factory=list)
    resources_changed: list[ResourceChange] = field(default_factory=list)
    resources_advisory: list[ResourceChange] = field(default_factory=list)
    # Kinds ('resource' | 'resource_template') the live server exposes but the
    # stored manifest has no section for. Advisory only.
    resources_untracked: list[str] = field(default_factory=list)
    # True when at least one resource section was present on both sides.
    resources_compared: bool = False
    # Set by the caller when listing resources failed while the stored manifest
    # tracks none, so nothing was compared. Advisory only.
    resources_error: str | None = None

    @property
    def has_drift(self) -> bool:
        """True when a tool or resource was added, removed, or had its contract change.

        Advisories (description or name differences, untracked resource
        sections) deliberately do not count.
        """
        return bool(
            self.added
            or self.removed
            or self.changed
            or self.resources_added
            or self.resources_removed
            or self.resources_changed
        )

    @property
    def advisory_count(self) -> int:
        return (
            len(self.advisory)
            + len(self.resources_advisory)
            + len(self.resources_untracked)
            + (1 if self.resources_error else 0)
        )


def _validate(m: dict, label: str) -> None:
    version = m.get("format_version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"{label} manifest has format_version {version!r}, expected {FORMAT_VERSION}. "
            f"Regenerate it with `mcpgen codegen`."
        )
    if not isinstance(m.get("tools"), dict):
        raise ValueError(f"{label} manifest has no 'tools' object")
    sections = ["tools"]
    for section, _kind in _RESOURCE_SECTIONS:
        if section in m:
            if not isinstance(m[section], dict):
                raise ValueError(f"{label} manifest has a non-object '{section}' section")
            sections.append(section)
    for section in sections:
        for key, entry in m[section].items():
            if not isinstance(entry, dict):
                raise ValueError(f"{label} manifest has a non-object '{section}' entry {key!r}")


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


def _mime_key(mime_type: Any) -> Any:
    """MIME type and subtype names are case-insensitive (RFC 2045 §5.1)."""
    return mime_type.lower() if isinstance(mime_type, str) else mime_type


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

    r_added: list[ResourceRef] = []
    r_removed: list[ResourceRef] = []
    r_changed: list[ResourceChange] = []
    r_advisory: list[ResourceChange] = []
    untracked: list[str] = []
    compared = False
    for section, kind in _RESOURCE_SECTIONS:
        live = new.get(section)
        if live is None:
            # Not collected live: nothing to compare against, whatever is stored.
            continue
        stored = old.get(section)
        if stored is None:
            if live:
                untracked.append(kind)
            continue
        compared = True
        r_added += [ResourceRef(kind, key) for key in sorted(set(live) - set(stored))]
        r_removed += [ResourceRef(kind, key) for key in sorted(set(stored) - set(live))]
        for key in sorted(set(stored) & set(live)):
            before, after = stored[key], live[key]
            if _mime_key(before.get("mimeType")) != _mime_key(after.get("mimeType")):
                r_changed.append(
                    ResourceChange(
                        kind,
                        key,
                        "mimeType",
                        f"mimeType changed: {before.get('mimeType')!r} -> {after.get('mimeType')!r}",
                        before.get("mimeType"),
                        after.get("mimeType"),
                    )
                )
                continue
            # As with tools, advisories are only reached when the contract
            # (mimeType) is unchanged, so they never mask drift. A missing key
            # and "" are the same text, as build() writes them.
            for attr in ("name", "description"):
                if (before.get(attr) or "") != (after.get(attr) or ""):
                    r_advisory.append(
                        ResourceChange(kind, key, attr, f"{attr} changed", before.get(attr), after.get(attr))
                    )

    return DriftReport(
        server=new.get("server", old.get("server", "")),
        added=added,
        removed=removed,
        changed=changed,
        advisory=advisory,
        resources_added=r_added,
        resources_removed=r_removed,
        resources_changed=r_changed,
        resources_advisory=r_advisory,
        resources_untracked=untracked,
        resources_compared=compared,
    )


_KIND_LABEL = {"resource": "resource", "resource_template": "resource template"}
_UNTRACKED_LABEL = {"resource": "resources", "resource_template": "resource templates"}


def render_text(report: DriftReport) -> str:
    """Human-readable drift report. Advisories print but never imply drift."""
    lines: list[str] = []
    for name in report.added:
        lines.append(f"ADDED     {report.server}.{name}")
    for name in report.removed:
        lines.append(f"REMOVED   {report.server}.{name}")
    for change in report.changed:
        lines.append(f"CHANGED   {report.server}.{change.tool}: {change.detail}")
    for ref in report.resources_added:
        lines.append(f"ADDED     {report.server} {_KIND_LABEL[ref.kind]} {ref.key}")
    for ref in report.resources_removed:
        lines.append(f"REMOVED   {report.server} {_KIND_LABEL[ref.kind]} {ref.key}")
    for rchange in report.resources_changed:
        lines.append(f"CHANGED   {report.server} {_KIND_LABEL[rchange.kind]} {rchange.key}: {rchange.detail}")
    for change in report.advisory:
        lines.append(f"ADVISORY  {report.server}.{change.tool}: {change.detail}")
        lines.append(f"            - {change.old!r}")
        lines.append(f"            + {change.new!r}")
    for rchange in report.resources_advisory:
        lines.append(f"ADVISORY  {report.server} {_KIND_LABEL[rchange.kind]} {rchange.key}: {rchange.detail}")
        lines.append(f"            - {rchange.old!r}")
        lines.append(f"            + {rchange.new!r}")
    for kind in report.resources_untracked:
        lines.append(
            f"ADVISORY  {report.server}: live {_UNTRACKED_LABEL[kind]} are not tracked by the stored manifest; "
            "re-run with `--update` to track them, or pass `--resources none` to skip them"
        )
    if report.resources_error:
        lines.append(
            f"ADVISORY  {report.server}: {report.resources_error}; "
            "the stored manifest tracks no resources, so none were compared"
        )

    advisories = report.advisory_count
    suffix = f" ({advisories} advisory)" if advisories else ""
    if report.has_drift:
        n_added = len(report.added) + len(report.resources_added)
        n_removed = len(report.removed) + len(report.resources_removed)
        n_changed = len(report.changed) + len(report.resources_changed)
        summary = f"Drift detected: {n_added} added, {n_removed} removed, {n_changed} changed{suffix}."
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


def _resource_change_to_json(change: ResourceChange) -> dict:
    return {
        "kind": change.kind,
        "key": change.key,
        "category": change.category,
        "detail": change.detail,
        "old": change.old,
        "new": change.new,
    }


def to_json(report: DriftReport) -> dict:
    """Structured drift report for CI consumers.

    The ``"resources"`` block appears only when a resource section was compared
    or is untracked, or listing resources failed, so tool-only reports keep
    their original shape. Its ``"error"`` key is present only for a failure.
    """
    payload: dict[str, Any] = {
        "server": report.server,
        "drift": report.has_drift,
        "added": list(report.added),
        "removed": list(report.removed),
        "changed": [_change_to_json(c) for c in report.changed],
        "advisory": [_change_to_json(c) for c in report.advisory],
    }
    if report.resources_compared or report.resources_untracked or report.resources_error:
        payload["resources"] = {
            "compared": report.resources_compared,
            "untracked": list(report.resources_untracked),
            "added": [{"kind": r.kind, "key": r.key} for r in report.resources_added],
            "removed": [{"kind": r.kind, "key": r.key} for r in report.resources_removed],
            "changed": [_resource_change_to_json(c) for c in report.resources_changed],
            "advisory": [_resource_change_to_json(c) for c in report.resources_advisory],
        }
        if report.resources_error:
            payload["resources"]["error"] = report.resources_error
    return payload
