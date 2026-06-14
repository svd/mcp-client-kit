"""Deterministic codegen: MCP tools/list -> typed Python wrapper source.

Pure functions only (no I/O) so they're trivially testable. The CLI wires these
to a live server via the bridge. Generated modules depend only on the McpCaller
seam (mcp_client_kit.seam), never on a concrete client.
"""
from __future__ import annotations

import keyword
import re
from typing import Any

# ── JSON Schema -> Python type ───────────────────────────────────────────────

_SCALARS = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
}


def py_type(schema: dict | None) -> str:
    """Map a JSON Schema fragment to a Python type annotation string."""
    if not schema or not isinstance(schema, dict):
        return "Any"

    # Union-ish constructs: punt to Any rather than guess wrong.
    if any(k in schema for k in ("anyOf", "oneOf", "allOf")):
        return "Any"

    t = schema.get("type")
    if isinstance(t, list):  # e.g. ["string", "null"]
        non_null = [x for x in t if x != "null"]
        inner = _SCALARS.get(non_null[0], "Any") if len(non_null) == 1 else "Any"
        return f"{inner} | None" if "null" in t else inner

    if t == "array":
        return f"list[{py_type(schema.get('items'))}]"
    if t == "object":
        return "dict"
    return _SCALARS.get(t, "Any")


# ── Identifier sanitization ──────────────────────────────────────────────────

def sanitize(name: str) -> str:
    """Turn an arbitrary tool/param name into a valid Python identifier."""
    s = re.sub(r"\W", "_", name)
    if not s or s[0].isdigit():
        s = "_" + s
    if keyword.iskeyword(s):
        s += "_"
    return s


# ── Rendering ────────────────────────────────────────────────────────────────

def _docstring(text: str | None, indent: str) -> str:
    text = (text or "").strip()
    if not text:
        return f'{indent}""" """'
    lines = text.splitlines()
    if len(lines) == 1:
        return f'{indent}"""{lines[0]}"""'
    body = f"\n{indent}".join(lines)
    return f'{indent}"""{body}\n{indent}"""'


def render_model(name: str, fields: dict[str, str]) -> str:
    """Render a probe-derived return model as a `TypedDict` (a hint, not validation).

    `total=False` because field projection means any field may be absent; only the
    top-level stable scalars the probe actually saw belong here (deep/variadic nests
    stay `Any` upstream). See doc/EVAL_RADAR.md and the skill's guards.
    """
    lines = [f"class {name}(TypedDict, total=False):"]
    if not fields:
        lines.append("    pass")
    else:
        for fname, ftype in fields.items():
            lines.append(f"    {fname}: {ftype}")
    return "\n".join(lines)


# Emitted once per module when any tool unwraps a vendor envelope. Mirrors the
# hand-built hand-built oracle `_unwrap_entity` semantics (return as-is on miss).
_DIG = '''def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    """Dig into a nested dict by key path; return obj unchanged if the path is absent."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return obj
        cur = cur[key]
    return cur'''


# Emitted once per module when any tool unwraps a vendor envelope to a LIST.
# Mirrors the hand-built hand-built oracle `_unwrap_results` (radar.py:119): an
# already-unwrapped list passes through, a full envelope is dug, otherwise fall
# back to the last path key at top level — defaulting to [] so the return is
# always a list (never the raw envelope dict, unlike `_dig`).
_DIG_LIST = '''def _dig_list(obj: Any, path: tuple[str, ...]) -> list:
    """Unwrap to a list at the given key path, honouring the list contract.

    A list passes through; a full envelope is dug; otherwise fall back to the last
    path key at top level, defaulting to [] (never a non-list)."""
    if isinstance(obj, list):
        return obj
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return obj.get(path[-1], []) if isinstance(obj, dict) else []
        cur = cur[key]
    return cur'''


def render_tool(tool: dict, shape: dict | None = None) -> str:
    """Render one MCP tool into a typed `async def` against the McpCaller seam.

    With a probe-derived `shape` (unwrap path / return model / input overrides),
    the wrapper unwraps the vendor envelope and returns the typed record instead
    of opaque `Any`. Without it, behaviour is identical to pure stub generation.
    """
    raw_name = tool["name"]
    fn = sanitize(raw_name)
    schema = tool.get("inputSchema") or {}
    props: dict = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    shape = shape or {}
    unwrap: list = shape.get("unwrap") or []
    return_model: str | None = shape.get("return_model")
    overrides: dict = shape.get("input_overrides") or {}
    # return_container="list" => the unwrapped value is a LIST of return_model
    # records (e.g. query_radar's data.results). Annotation/cast become
    # list[Model] and the body digs via _dig_list. Default (None) = dict/scalar.
    container: str | None = shape.get("return_container")
    if return_model:
        ret_ann = f"list[{return_model}]" if container == "list" else return_model
    else:
        ret_ann = "Any"

    # required params first (no default), then optional (= None).
    ordered = sorted(props.items(), key=lambda kv: kv[0] not in required)

    params: list[str] = []
    body_args: list[str] = []
    for pname, pschema in ordered:
        py = sanitize(pname)
        ann = overrides.get(pname) or py_type(pschema)
        if pname in required:
            params.append(f"{py}: {ann}")
            body_args.append((py, pname, True))
        else:
            opt_ann = ann if ann.endswith("| None") else f"{ann} | None"
            params.append(f"{py}: {opt_ann} = None")
            body_args.append((py, pname, False))

    sig_params = ["caller: McpCaller", "*", *params] if params else ["caller: McpCaller"]
    sig = ", ".join(sig_params)

    lines = [f"async def {fn}({sig}) -> {ret_ann}:"]
    lines.append(_docstring(tool.get("description"), "    "))

    req_pairs = [(py, pname) for py, pname, is_req in body_args if is_req]
    opt_pairs = [(py, pname) for py, pname, is_req in body_args if not is_req]

    if not body_args:
        args_expr = "{}"
    else:
        if req_pairs:
            inits = ", ".join(f'"{pname}": {py}' for py, pname in req_pairs)
            lines.append(f"    args: dict[str, Any] = {{{inits}}}")
        else:
            lines.append("    args: dict[str, Any] = {}")
        for py, pname in opt_pairs:
            lines.append(f"    if {py} is not None:")
            lines.append(f'        args["{pname}"] = {py}')
        args_expr = "args"

    call = f'await caller.call(SERVER, "{raw_name}", {args_expr})'
    if unwrap:
        path = "(" + "".join(f"{k!r}, " for k in unwrap) + ")"
        lines.append(f"    result = {call}")
        digger = "_dig_list" if container == "list" else "_dig"
        dug = f"{digger}(result, {path})"
        ret = f'cast("{ret_ann}", {dug})' if return_model else dug
        lines.append(f"    return {ret}")
    elif return_model:
        lines.append(f'    return cast("{ret_ann}", {call})')
    else:
        lines.append(f"    return {call}")

    return "\n".join(lines)


_HEADER = '''"""Generated MCP wrappers for the {server!r} server. DO NOT hand-edit signatures.

Generated by mcp-client-kit from a live tools/list. Each function forwards to an
injected McpCaller (see mcp_client_kit.seam); the auth/transport backend is the
caller's concern, not this module's.
{probe_note}"""
from __future__ import annotations

{imports}

from mcp_client_kit.seam import McpCaller

SERVER = {server!r}
'''


def render_module(server: str, tools: list[dict], shapes: dict | None = None,
                  probe_note: str = "") -> str:
    """Render a full wrapper module.

    `shapes` maps tool name -> probe-derived shape-spec (see render_tool). When
    present, the module gains TypedDict return models and a `_dig` unwrap helper;
    the no-shapes path is byte-identical to pure stub generation.
    """
    shapes = shapes or {}
    note = f"\n{probe_note}\n" if probe_note else ""
    imports = "from typing import Any, TypedDict, cast" if shapes else "from typing import Any"
    parts = [_HEADER.format(server=server, probe_note=note, imports=imports)]

    if shapes:
        models = []
        for tool in sorted(tools, key=lambda t: t["name"]):
            sp = shapes.get(tool["name"])
            if sp and sp.get("return_model"):
                models.append(render_model(sp["return_model"], sp.get("fields") or {}))
        parts.extend(models)
        specs = [shapes.get(t["name"]) or {} for t in tools]
        if any(s.get("unwrap") and s.get("return_container") != "list" for s in specs):
            parts.append(_DIG)
        if any(s.get("unwrap") and s.get("return_container") == "list" for s in specs):
            parts.append(_DIG_LIST)

    for tool in sorted(tools, key=lambda t: t["name"]):
        parts.append(render_tool(tool, shapes.get(tool["name"])))
    return "\n\n\n".join(parts) + "\n"


# ── Response-shape summary (for the empirical probe) ─────────────────────────

def summarize_shape(obj: Any, max_keys: int = 40, max_depth: int = 6,
                    _depth: int = 0) -> Any:
    """Reduce a live response to a compact shape (keys + types + nesting).

    Records structure, NOT payload — radar responses are 100-500 KB and must not
    be embedded wholesale. Lists collapse to their first element's shape.
    """
    if _depth >= max_depth:
        return "..."
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= max_keys:
                out["...(+%d keys)" % (len(obj) - max_keys)] = None
                break
            out[k] = summarize_shape(v, max_keys, max_depth, _depth + 1)
        return out
    if isinstance(obj, list):
        if not obj:
            return ["<empty>"]
        return [summarize_shape(obj[0], max_keys, max_depth, _depth + 1),
                f"...x{len(obj)}"] if len(obj) > 1 else [
            summarize_shape(obj[0], max_keys, max_depth, _depth + 1)]
    return type(obj).__name__
