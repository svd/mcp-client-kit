"""Deterministic codegen: MCP tools/list -> typed Python wrapper source.

Pure functions only (no I/O) so they're trivially testable. The CLI wires these
to a live server via the bridge. Generated modules depend only on the McpCaller
seam (mcpgen.seam), never on a concrete client.
"""

from __future__ import annotations

import keyword
import re
import sys
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

    # Enum: emit Literal[...] only when every member is a PEP 586-safe scalar.
    # Floats, lists, and dicts are not valid Literal parameters; fall through to
    # the type-based path so we emit the declared type (or Any) instead.
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        if all(type(v) in (str, bool, int, bytes, type(None)) for v in enum_vals):
            t = schema.get("type")
            nullable = isinstance(t, list) and "null" in t
            inner = "Literal[" + ", ".join(repr(v) for v in enum_vals) + "]"
            return f"{inner} | None" if nullable else inner

    t = schema.get("type")
    if isinstance(t, list):  # e.g. ["string", "null"]
        non_null = [x for x in t if x != "null"]
        inner = _SCALARS.get(non_null[0], "Any") if len(non_null) == 1 else "Any"
        return f"{inner} | None" if "null" in t else inner

    if t == "array":
        return f"list[{py_type(schema.get('items'))}]"
    if t == "object":
        return "dict"
    return _SCALARS.get(t or "", "Any")


# ── Shape-spec annotation normalization ──────────────────────────────────────

# JSON/TS-cased tokens that need to be replaced with their Python equivalents
# when they appear in hand-edited shapes.json values.
_ANNOTATION_REWRITE: dict[str, str] = {
    "any": "Any",
    "null": "None",
    "none": "None",
    **{k: v for k, v in _SCALARS.items() if k != "null"},  # string→str, integer→int, etc.
}
_ANNOTATION_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _ANNOTATION_REWRITE) + r")\b")


def normalize_annotation(s: str) -> str:
    """Rewrite JSON/TS-cased type tokens to their Python equivalents.

    Uses word-boundary matching so custom identifiers such as ``AnyThing`` or
    ``NullableModel`` are left untouched.  Already-correct strings (``Any``,
    ``None``, ``str`` …) pass through unchanged.

    Examples::

        normalize_annotation("any")           == "Any"
        normalize_annotation("null")          == "None"
        normalize_annotation("list[any]")     == "list[Any]"
        normalize_annotation("str | null")    == "str | None"
        normalize_annotation("AnyThing")      == "AnyThing"   # unchanged
    """
    return _ANNOTATION_RE.sub(lambda m: _ANNOTATION_REWRITE[m.group()], s)


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


def _str_literal(s: str) -> str:
    """Emit a Python string literal for *s* that is injection-safe.

    Uses a plain double-quoted literal when the value contains no characters
    that could break out of ``"…"`` (preserving byte-identical output for
    well-formed names); falls back to ``repr()``, which always produces a
    valid, injection-safe literal.
    """
    if not any(c in s for c in ('"', "\\", "\n", "\r")):
        return f'"{s}"'
    return repr(s)


_LARGE_PAYLOAD_THRESHOLD_BYTES = 64 * 1024


def _size_note(shape: dict | None) -> str | None:
    """Docstring note when a probe recorded an unusually large response size."""
    n = (shape or {}).get("_observed_bytes")
    if not isinstance(n, (int, float)) or n < _LARGE_PAYLOAD_THRESHOLD_BYTES:
        return None
    kb = round(n / 1024)
    return f"Returns a large payload (~{kb} KB observed) — prefer narrower queries where available."


def _with_size_note(desc: str | None, shape: dict | None) -> str | None:
    note = _size_note(shape)
    if not note:
        return desc
    desc = (desc or "").strip()
    return f"{desc}\n\n{note}" if desc else note


def _docstring(text: str | None, indent: str) -> str:
    text = (text or "").strip()
    if not text:
        return f'{indent}""" """'
    # Triple-quote form is safe only when the text cannot break out of it:
    # no backslash, no triple-quote sequence, and not ending with a quote
    # char (which would form an accidental close sequence like `foo"""""`).
    if "\\" not in text and '"""' not in text and not text.endswith('"'):
        lines = text.splitlines()
        if len(lines) == 1:
            return f'{indent}"""{lines[0]}"""'
        body = f"\n{indent}".join(lines)
        return f'{indent}"""{body}\n{indent}"""'
    # Server-controlled text with special chars — use a repr() literal.
    # repr() always produces a valid, injection-safe Python string literal.
    return f"{indent}{repr(text)}"


def render_model(name: str, fields: dict[str, str]) -> str:
    """Render a probe-derived return model as a `TypedDict` (a hint, not validation).

    `total=False` because field projection means any field may be absent; only the
    top-level stable scalars the probe actually saw belong here (deep/variadic nests
    stay `Any` upstream). See the skill's guards.
    """
    lines = [f"class {name}(TypedDict, total=False):"]
    if not fields:
        lines.append("    pass")
    else:
        for fname, ftype in fields.items():
            lines.append(f"    {fname}: {ftype}")
    return "\n".join(lines)


# Emitted once per module when any tool unwraps a vendor envelope. Mirrors the
# hand-built oracle wrapper's `_unwrap_entity` semantics (return as-is on miss).
# Also handles double-serialized responses where the MCP TextContent text field
# is itself a JSON-encoded string (e.g. sqlite, github describe_table).
_DIG = '''def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    """Dig into a nested dict by key path; return obj unchanged if the path is absent.
    If obj or the extracted value is a JSON-encoded string, parses it first."""
    raw = obj
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            pass
    cur = raw
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return raw
        cur = cur[key]
    if isinstance(cur, str):
        try:
            return json.loads(cur)
        except (ValueError, TypeError):
            pass
    return cur'''


# Emitted once per module when any tool unwraps a vendor envelope to a LIST.
# Mirrors the hand-built oracle's `_unwrap_results`: an already-unwrapped list
# passes through, a full envelope is dug, otherwise fall back to the last path
# key at top level — defaulting to [] so the return is always a list (never
# the raw envelope dict, unlike `_dig`).
# Also handles double-serialized responses (same as _dig).
_DIG_LIST = '''def _dig_list(obj: Any, path: tuple[str, ...]) -> list:
    """Unwrap to a list at the given key path, honouring the list contract.

    A list passes through; a full envelope is dug; otherwise fall back to the last
    path key at top level, defaulting to [] (never a non-list).
    If obj or the extracted value is a JSON-encoded string, parses it first."""
    if isinstance(obj, list):
        return obj
    raw = obj
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
            if isinstance(raw, list):
                return raw
        except (ValueError, TypeError):
            pass
    if not isinstance(raw, dict):
        return []
    cur = raw
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return raw.get(path[-1], [])
        cur = cur[key]
    if isinstance(cur, str):
        try:
            parsed = json.loads(cur)
            if isinstance(parsed, list):
                return parsed
        except (ValueError, TypeError):
            pass
    return cur'''


def _docstring_with_args(tool: dict, ordered: list[tuple[str, dict]], indent: str, shape: dict | None = None) -> str:
    """Build docstring with description + Args section from inputSchema properties."""
    desc = _with_size_note((tool.get("description") or "").strip(), shape) or ""
    props_lines = []
    for pname, pschema in ordered:
        py = sanitize(pname)
        parts: list[str] = []
        if not isinstance(pschema, dict):
            props_lines.append(f"    {py}:")
            continue
        pdesc = (pschema.get("description") or "").strip()
        if pdesc:
            parts.append(pdesc)
        if pschema.get("enum"):
            parts.append(f"One of: {', '.join(repr(v) for v in pschema['enum'])}")
        if "default" in pschema:
            parts.append(f"Default: {pschema['default']!r}")
        detail = ". ".join(parts)
        if detail:
            props_lines.append(f"    {py}: {detail}")
        else:
            props_lines.append(f"    {py}:")

    if not props_lines:
        return _docstring(desc or None, indent)

    if desc:
        body = desc + "\n\nArgs:\n" + "\n".join(props_lines)
    else:
        body = "Args:\n" + "\n".join(props_lines)

    # Use repr() to ensure injection safety (description is server-controlled text)
    # But we want multi-line docstring format where possible.
    # Fall back to repr() only if text contains backslash, triple-quotes, or trailing quote.
    text = body.strip()
    if "\\" in text or '"""' in text or text.endswith('"'):
        return f"{indent}{repr(text)}"
    lines_list = text.splitlines()
    if len(lines_list) == 1:
        return f'{indent}"""{lines_list[0]}"""'
    parts = [lines_list[0]] + [f"{indent}{ln}" if ln else "" for ln in lines_list[1:]]
    joined = "\n".join(parts)
    return f'{indent}"""{joined}\n{indent}"""'


def _variants_are_numeric(variants: dict) -> bool:
    """True iff every discriminator variant key parses as an int.

    Fallback only — used when the discriminator's declared schema type can't be
    determined (see `_discriminator_type`). Sniffing keys alone is unsound:
    a `type: "string"` discriminator can have numeric-looking enum values
    (e.g. `"1"`, `"2"`), which would be misclassified as int.
    """
    if not variants:
        return True
    for k in variants:
        try:
            int(k)
        except ValueError:
            return False
    return True


def _discriminator_type(disc: str, props: dict, overrides: dict, variants: dict) -> str:
    """The discriminator argument's Python type — `"int"` or `"str"` — schema/override first.

    Priority: an explicit `input_overrides` entry for the discriminator, then the
    tool's declared `inputSchema` type for that property (a JSON Schema `type` may
    also be a list, e.g. `["string", "null"]` — if the list names both `"integer"`
    and `"string"`, `"integer"` wins), then (only if neither is available) a
    best-effort sniff of the shape-spec's variant keys.

    `float` is deliberately unsupported: `typing.Literal` cannot hold float values,
    so a float discriminator can't be expressed as `@overload` stubs. An explicit
    `"float"` override raises; a schema `type: "number"` (which permits floats)
    falls back to key-sniffing rather than assuming int, so real float variant
    keys (e.g. `"1.5"`) resolve to `"str"` instead of crashing on `int()`.

    Any other explicit override (not `int`/`str`/`float`) also raises — a
    discriminator's `Literal` type can only ever be `int` or `str`, so silently
    ignoring an unrecognized override (e.g. a typo like `"strr"`) would drop
    the user's intent without warning.
    """
    override = overrides.get(disc)
    if override == "int":
        return "int"
    if override == "str":
        return "str"
    if override is not None:
        raise ValueError(
            f"discriminator {disc!r} has unsupported input_overrides type {override!r} — "
            "only 'int' or 'str' are valid for a discriminator (Literal can't hold floats "
            "or other types)"
        )
    pschema = props.get(disc)
    if isinstance(pschema, dict):
        t = pschema.get("type")
        types = t if isinstance(t, list) else [t]
        if "integer" in types:
            return "int"
        if "string" in types:
            return "str"
        if "number" in types:
            return "int" if _variants_are_numeric(variants) else "str"
    return "int" if _variants_are_numeric(variants) else "str"


def _variant_key(k: str, disc: str, numeric: bool) -> int | str:
    """Coerce a variant key to the discriminator's resolved type, erroring with context on mismatch."""
    if not numeric:
        return k
    try:
        return int(k)
    except ValueError:
        raise ValueError(
            f"discriminator {disc!r} resolved to int, but variant key {k!r} isn't a valid int — "
            "fix the shape-spec's input_overrides/variant keys or the tool's inputSchema type"
        ) from None


def _get_overrides(shape: dict) -> dict:
    """`input_overrides` sidecar entry, validated as a dict.

    A hand-edited shape-spec can set this to the wrong JSON type (e.g. a list,
    from a typo); failing here with a clear message beats a confusing
    `AttributeError` from `.get()` deep inside signature rendering.
    """
    overrides = shape.get("input_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"shape-spec input_overrides must be a dict, got {type(overrides).__name__}: {overrides!r}")
    return overrides


def _discriminator_context(tool: dict, shape: dict) -> tuple[str, bool]:
    """Resolve (discriminator name, is-numeric) for a discriminated-union shape-spec.

    Shared by `_render_overloaded` (signature/Literal generation) and
    `render_module` (return-model registration) so the two call sites can't
    drift on discriminator-type resolution.
    """
    disc: str = shape["discriminator"]
    variants: dict = shape["variants"]
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    numeric = _discriminator_type(disc, props, _get_overrides(shape), variants) == "int"
    return disc, numeric


def _render_overloaded(
    tool: dict,
    fn: str,
    raw_name: str,
    props: dict,
    required: set,
    shape: dict,
    embed_schema: bool = False,
    schema: dict | None = None,
) -> str:
    """Emit @overload stubs (one per discriminator value) + impl signature."""
    variants: dict = shape["variants"]
    unwrap: list = shape.get("unwrap") or []
    overrides: dict = _get_overrides(shape)
    container: str | None = shape.get("return_container")

    disc, numeric = _discriminator_context(tool, shape)
    disc_type = "int" if numeric else "str"

    ordered = sorted(props.items(), key=lambda kv: kv[0] not in required)
    coerced_variants = sorted(
        ((_variant_key(k, disc, numeric), v) for k, v in variants.items()), key=lambda kv: kv[0]
    )

    def _build_params(disc_type: str) -> list[str]:
        out = []
        for pname, pschema in ordered:
            py = sanitize(pname)
            ann = overrides.get(pname) or py_type(pschema)
            if pname == disc:
                out.append(f"{py}: {disc_type}")
            elif pname in required:
                out.append(f"{py}: {ann}")
            else:
                opt_ann = ann if ann.endswith("| None") else f"{ann} | None"
                out.append(f"{py}: {opt_ann} = None")
        return out

    def _sig(params: list[str]) -> str:
        all_p = ["caller: McpCaller", "*", *params] if params else ["caller: McpCaller"]
        return ", ".join(all_p)

    variant_models = [
        (val, v.get("return_model", "Any")) for val, v in coerced_variants if v.get("return_model")
    ]
    union_ret = " | ".join(m for _, m in variant_models) if variant_models else "Any"

    blocks: list[str] = []

    for val, model in variant_models:
        ret = f"list[{model}]" if container == "list" else model
        literal = str(val) if numeric else repr(val)
        params = _build_params(f"Literal[{literal}]")
        blocks.append(f"@overload\nasync def {fn}({_sig(params)}) -> {ret}: ...")

    impl_ret = f"list[{union_ret}]" if container == "list" else union_ret
    impl_lines = [f"async def {fn}({_sig(_build_params(disc_type))}) -> {impl_ret}:"]
    if embed_schema:
        impl_lines.append(_docstring_with_args(tool, ordered, "    ", shape=shape))
    else:
        impl_lines.append(_docstring(_with_size_note(tool.get("description"), shape), "    "))

    body_args = [(sanitize(pname), pname, pname in required or pname == disc) for pname, _ in ordered]
    req_pairs = [(py, pname) for py, pname, is_req in body_args if is_req]
    opt_pairs = [(py, pname) for py, pname, is_req in body_args if not is_req]

    if not body_args:
        args_expr = "{}"
    else:
        if req_pairs:
            inits = ", ".join(f"{_str_literal(pname)}: {py}" for py, pname in req_pairs)
            impl_lines.append(f"    args: dict[str, Any] = {{{inits}}}")
        else:
            impl_lines.append("    args: dict[str, Any] = {}")
        for py, pname in opt_pairs:
            impl_lines.append(f"    if {py} is not None:")
            impl_lines.append(f"        args[{_str_literal(pname)}] = {py}")
        args_expr = "args"

    call = f"await caller.call(SERVER, {_str_literal(raw_name)}, {args_expr})"
    if unwrap:
        path = "(" + "".join(f"{k!r}, " for k in unwrap) + ")"
        impl_lines.append(f"    result = {call}")
        digger = "_dig_list" if container == "list" else "_dig"
        dug = f"{digger}(result, {path})"
        impl_lines.append(f'    return cast("{impl_ret}", {dug})')
    else:
        impl_lines.append(f'    return cast("{impl_ret}", {call})')

    impl_block = "\n".join(impl_lines)
    if embed_schema:
        _schema = schema if schema is not None else {}
        impl_block += f"\n\n{fn}.__schema__ = {repr(_schema)}"
    blocks.append(impl_block)
    return "\n\n".join(blocks)


def render_tool(tool: dict, shape: dict | None = None, embed_schema: bool = False) -> str:
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
    if shape.get("discriminator") and shape.get("variants"):
        return _render_overloaded(tool, fn, raw_name, props, required, shape, embed_schema=embed_schema, schema=schema)

    unwrap: list = shape.get("unwrap") or []
    return_model: str | None = shape.get("return_model")
    overrides: dict = _get_overrides(shape)
    # return_container="list" => the unwrapped value is a LIST of return_model
    # records (e.g. query_acme's data.results). Annotation/cast become
    # list[Model] and the body digs via _dig_list. Default (None) = dict/scalar.
    container: str | None = shape.get("return_container")
    if return_model:
        ret_ann = f"list[{return_model}]" if container == "list" else return_model
    else:
        ret_ann = "Any"

    # required params first (no default), then optional (= None).
    ordered = sorted(props.items(), key=lambda kv: kv[0] not in required)

    params: list[str] = []
    body_args: list[tuple[str, Any, bool]] = []
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
    if embed_schema:
        lines.append(_docstring_with_args(tool, ordered, "    ", shape=shape))
    else:
        lines.append(_docstring(_with_size_note(tool.get("description"), shape), "    "))

    req_pairs = [(py, pname) for py, pname, is_req in body_args if is_req]
    opt_pairs = [(py, pname) for py, pname, is_req in body_args if not is_req]

    if not body_args:
        args_expr = "{}"
    else:
        if req_pairs:
            inits = ", ".join(f"{_str_literal(pname)}: {py}" for py, pname in req_pairs)
            lines.append(f"    args: dict[str, Any] = {{{inits}}}")
        else:
            lines.append("    args: dict[str, Any] = {}")
        for py, pname in opt_pairs:
            lines.append(f"    if {py} is not None:")
            lines.append(f"        args[{_str_literal(pname)}] = {py}")
        args_expr = "args"

    call = f"await caller.call(SERVER, {_str_literal(raw_name)}, {args_expr})"
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

    result = "\n".join(lines)
    if embed_schema:
        result += f"\n\n{fn}.__schema__ = {repr(schema)}"
    return result


_HEADER = '''"""Generated MCP wrappers for the {server!r} server. DO NOT hand-edit signatures.

Generated by mcpgen from a live tools/list. Each function forwards to an
injected McpCaller (see mcpgen.seam); the auth/transport backend is the
caller's concern, not this module's.
{probe_note}"""
from __future__ import annotations

{imports}

from mcpgen.seam import McpCaller

SERVER = {server!r}
'''


_PY_BUILTINS = frozenset({"str", "int", "float", "list", "dict", "bool", "bytes", "object", "type", "set", "tuple"})


def render_module(
    server: str, tools: list[dict], shapes: dict | None = None, probe_note: str = "", embed_schema: bool = False
) -> str:
    """Render a full wrapper module.

    `shapes` maps tool name -> probe-derived shape-spec (see render_tool). When
    present, the module gains TypedDict return models and a `_dig` unwrap helper;
    the no-shapes path is byte-identical to pure stub generation.
    """
    shapes = shapes or {}
    note = f"\n{probe_note}\n" if probe_note else ""
    has_disc = any(s.get("discriminator") and s.get("variants") for s in shapes.values())
    needs_json = bool(shapes) and any((shapes.get(t["name"]) or {}).get("unwrap") for t in tools)
    has_typed_return = has_disc or any(s.get("return_model") for s in shapes.values())

    def _schema_has_enum(s: dict | None) -> bool:
        if not isinstance(s, dict):
            return False
        if s.get("enum"):
            return True
        # recurse into array items (py_type can emit Literal for nested enums)
        items = s.get("items")
        return isinstance(items, dict) and bool(items.get("enum"))

    has_enum = any(
        any(_schema_has_enum(pschema) for pschema in (((t.get("inputSchema") or {}).get("properties") or {}).values()))
        for t in tools
    )
    if has_disc:
        type_imports = "from typing import Any, Literal, TypedDict, cast, overload"
    elif has_typed_return and has_enum:
        type_imports = "from typing import Any, Literal, TypedDict, cast"
    elif has_typed_return:
        type_imports = "from typing import Any, TypedDict, cast"
    elif has_enum:
        type_imports = "from typing import Any, Literal"
    else:
        type_imports = "from typing import Any"
    imports = ("import json\n" + type_imports) if needs_json else type_imports
    parts = [_HEADER.format(server=server, probe_note=note, imports=imports)]

    if shapes:
        models: list[str] = []
        _seen_models: dict[str, str] = {}  # name → rendered body; dedup across tools

        def _append_model(name: str, fields: dict) -> None:
            if name in _PY_BUILTINS:
                print(
                    f"[codegen] ⚠  return_model {name!r} is a Python builtin — ignored; use null instead",
                    file=sys.stderr,
                )
                return
            body = render_model(name, fields)
            if name not in _seen_models:
                _seen_models[name] = body
                models.append(body)
            elif _seen_models[name] == body:
                pass  # identical duplicate — skip silently
            else:
                # True shape collision: same name, different fields → emit suffixed variant.
                suffix = 2
                while f"{name}_{suffix}" in _seen_models:
                    suffix += 1
                suffixed = f"{name}_{suffix}"
                suffixed_body = render_model(suffixed, fields)
                _seen_models[suffixed] = suffixed_body
                models.append(suffixed_body)
                print(f"[codegen] ⚠  shape collision for {name!r}; emitted {suffixed!r}", file=sys.stderr)

        for tool in sorted(tools, key=lambda t: t["name"]):
            sp = shapes.get(tool["name"])
            if not sp:
                continue
            if sp.get("discriminator") and sp.get("variants"):
                variants = sp["variants"]
                disc, numeric = _discriminator_context(tool, sp)
                for _, variant in sorted(variants.items(), key=lambda kv: _variant_key(kv[0], disc, numeric)):
                    if variant.get("return_model"):
                        _append_model(variant["return_model"], variant.get("fields") or {})
            elif sp.get("return_model"):
                _append_model(sp["return_model"], sp.get("fields") or {})
        parts.extend(models)
        specs = [shapes.get(t["name"]) or {} for t in tools]
        if any(s.get("unwrap") and s.get("return_container") != "list" for s in specs):
            parts.append(_DIG)
        if any(s.get("unwrap") and s.get("return_container") == "list" for s in specs):
            parts.append(_DIG_LIST)

    for tool in sorted(tools, key=lambda t: t["name"]):
        parts.append(render_tool(tool, shapes.get(tool["name"]), embed_schema=embed_schema))
    return "\n\n\n".join(parts) + "\n"


# ── Response-shape summary (for the empirical probe) ─────────────────────────


def summarize_shape(obj: Any, max_keys: int = 40, max_depth: int = 6, _depth: int = 0) -> Any:
    """Reduce a live response to a compact shape (keys + types + nesting).

    Records structure, NOT payload — server responses can be 100-500 KB and must not
    be embedded wholesale. Lists collapse to their first element's shape.
    """
    if _depth >= max_depth:
        return "..."
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= max_keys:
                out[f"...(+{len(obj) - max_keys} keys)"] = None
                break
            out[k] = summarize_shape(v, max_keys, max_depth, _depth + 1)
        return out
    if isinstance(obj, list):
        if not obj:
            return ["<empty>"]
        return (
            [summarize_shape(obj[0], max_keys, max_depth, _depth + 1), f"...x{len(obj)}"]
            if len(obj) > 1
            else [summarize_shape(obj[0], max_keys, max_depth, _depth + 1)]
        )
    return type(obj).__name__


# ── Multi-probe shape merge ───────────────────────────────────────────────────


def _merge_scalar(types: set[str]) -> str:
    """Merge a set of observed type-name strings into one annotation.

    Priority rules:
    - Single type → identity.
    - NoneType only → "Any | None" (null seen but no concrete type known).
    - One concrete type + NoneType → "T | None" (nullable).
    - int + float (numeric widening; JSON numbers may be either) → "float".
    - Any other multi-concrete conflict → "Any" (honest; never wrong-guess).
    """
    if len(types) == 1:
        t = next(iter(types))
        return "Any | None" if t == "NoneType" else t
    has_none = "NoneType" in types
    concrete = types - {"NoneType"}
    if not concrete:
        return "Any | None"
    if concrete == {"int", "float"}:
        return "float | None" if has_none else "float"
    if len(concrete) == 1:
        t = next(iter(concrete))
        return f"{t} | None" if has_none else t
    # multi-concrete conflict
    return "Any | None" if has_none else "Any"


def merge_shapes(shapes: list[Any]) -> Any:
    """Deep-merge N summarize_shape() results into one representative shape.

    Used when probing a tool multiple times so the shape-spec reflects the
    union of all observed responses rather than a single sample.

    Merge rules:
    - Single input → returned unchanged (identity; N=1 stays byte-stable).
    - All dicts → key union; each key's values merged recursively across
      the shapes that carry that key.
    - All scalar strings (type names from summarize_shape) → _merge_scalar.
    - All lists → merge element shapes; discard "...xN" / "<empty>" sentinels.
    - Mixed kinds → "Any" (structural conflict; honest fallback).
    """
    if not shapes:
        return "Any"
    if len(shapes) == 1:
        return shapes[0]

    dicts = [s for s in shapes if isinstance(s, dict)]
    lists = [s for s in shapes if isinstance(s, list)]
    scalars = [s for s in shapes if isinstance(s, str)]

    if len(dicts) == len(shapes):
        # Union all keys; merge each key's values across the dicts that carry it.
        all_keys: dict[str, list[Any]] = {}
        for d in dicts:
            for k, v in d.items():
                all_keys.setdefault(k, []).append(v)
        return {k: merge_shapes(vs) for k, vs in all_keys.items()}

    if len(scalars) == len(shapes):
        return _merge_scalar(set(scalars))

    if len(lists) == len(shapes):
        # Each list is [elem_shape], [elem_shape, "...xN"], or ["<empty>"].
        # Collect actual element shapes, skip sentinels.
        elem_shapes: list[Any] = []
        for lst in lists:
            for item in lst:
                if isinstance(item, str) and (item.startswith("...x") or item == "<empty>"):
                    continue
                elem_shapes.append(item)
        return [merge_shapes(elem_shapes)] if elem_shapes else ["<empty>"]

    # Mixed kinds → structural conflict.
    return "Any"


def detect_discriminators(tools: list[dict]) -> dict[str, list[str]]:
    """Cross-analyse tool dicts and return discriminator-candidate params.

    Args:
        tools: List of MCP tool dicts with keys name, description, inputSchema.

    Returns:
        Mapping of candidate param name to sorted list of tool names that carry
        it. Returns scalar (integer/number/string) params that appear in ≥2
        tools, excluding common pagination/routing/path params that are never
        shape discriminators.
    """
    _SCALAR_TYPES = {"integer", "number", "string"}

    # Params that are routinely shared across tools but never discriminate response
    # shape — pagination, routing, path, and common filter args.
    # Comparison uses pname.lower(), so include common camelCase compound forms
    # (e.g. "repoName".lower() == "reponame", not "repo").
    _DENYLIST = {
        "page",
        "per_page",
        "limit",
        "offset",
        "cursor",
        "path",
        "repo",
        "owner",
        "org",
        "branch",
        "ref",
        "method",
        "query",
        "search",
        "filter",
        "sort",
        "order",
        "direction",
        "context_lines",
        "include",
        "exclude",
        "reponame",
        "repo_name",
        "repositoryname",
        "username",
        "orgname",
    }

    # candidates[param_name] = [tool_name, ...]
    candidates: dict[str, list[str]] = {}

    for tool in tools:
        tool_name = tool.get("name", "")
        schema = tool.get("inputSchema") or {}
        props: dict = schema.get("properties") or {}
        for pname, pschema in props.items():
            if pname.lower() in _DENYLIST:
                continue
            if not isinstance(pschema, dict):
                continue
            ptype = pschema.get("type")
            if ptype not in _SCALAR_TYPES:
                continue
            # Track all scalar params; the ≥2-tools filter in the return
            # covers the shared-scalar heuristic. Heuristic-name and enum
            # params are also scalar, so they're captured here too.
            candidates.setdefault(pname, []).append(tool_name)

    # Keep only params that appear in ≥2 tools
    return {pname: sorted(tool_names) for pname, tool_names in sorted(candidates.items()) if len(tool_names) >= 2}


def merge_skeletons(skeletons: list[dict]) -> dict:
    """Union per-tool skeleton dicts by tool name; later entries win.

    Used by `mcpgen merge` to consolidate per-tool part files into a single
    shapes.json, and by `_load_shapes` as an in-memory fallback when no merged
    file exists yet.  Distinct from merge_shapes(), which merges observed response
    shapes within one tool's probe calls.
    """
    out: dict = {}
    for sk in skeletons:
        out.update(sk)
    return out


def probe_skeleton(
    tool: str, args_list: list[dict], shapes: list[Any], observed_bytes: list[int] | None = None
) -> dict:
    """Build a shape-spec skeleton from N probe calls.

    args_list      — the N arg-dicts passed to the tool (one per probe).
    shapes         — the N summarize_shape() results from those calls.
    observed_bytes — optional raw response sizes (one per probe); the largest
                     is recorded as `_observed_shape` so the generated docstring
                     can warn callers about outsized payloads. Omitted entirely
                     when not passed (byte-stable with existing shapes.json).

    When args_list has a single entry, probed_args is that dict (byte-stable
    with current single-probe output). With multiple entries, probed_args is
    the full list — callers must scrub PII from each entry before committing.
    """
    merged = merge_shapes(shapes)
    # Normalize raw type-name strings (e.g. "NoneType" → "Any | None") so
    # fields values are valid Python annotation strings, not Python type names.
    fields: dict[str, str] = (
        {k: _merge_scalar({v}) for k, v in merged.items() if isinstance(v, str)} if isinstance(merged, dict) else {}
    )
    probed_args: Any = args_list[0] if len(args_list) == 1 else list(args_list)
    entry: dict[str, Any] = {
        "unwrap": [],
        "return_model": None,
        "input_overrides": {},
        "fields": fields,
        "source": "live",
        "probed_args": probed_args,
        "_observed_shape": merged,
    }
    if observed_bytes:
        entry["_observed_bytes"] = max(observed_bytes)
    return {tool: entry}
