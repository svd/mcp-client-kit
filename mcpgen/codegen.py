"""Deterministic codegen: MCP tools/list (+ resources) -> typed Python wrapper source.

Pure functions only (no I/O) so they're trivially testable; warnings go to
stderr. The CLI wires these to a live server via the bridge. Generated modules
depend only on the seam Protocols (mcpgen.seam), never on a concrete client.
"""

from __future__ import annotations

import ast
import keyword
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ── JSON Schema -> Python type ───────────────────────────────────────────────

_SCALARS = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
}


# PEP 586 admits these as Literal parameters; floats, lists and dicts are not.
_LITERAL_SAFE = (str, bool, int, bytes, type(None))


def _literal_safe(vals: list) -> bool:
    return all(type(v) in _LITERAL_SAFE for v in vals)


def _literal(vals: list) -> str:
    return "Literal[" + ", ".join(repr(v) for v in vals) + "]"


def _closed_values(branch: dict) -> list | None:
    """The values a union branch pins via `const` or a non-empty `enum`, or None if open."""
    if "const" in branch:
        return [branch["const"]]
    vals = branch.get("enum")
    return vals if isinstance(vals, list) and vals else None


def _union_literal(schema: dict) -> str | None:
    """`Literal[...]` for an anyOf/oneOf whose branches are all const/enum or null.

    A branch with a `const` or a non-empty `enum` is closed whatever other keys
    it carries; one with neither is a null branch if its `type` is "null", and
    open otherwise. A null branch, a `None` value or a sibling `type` containing
    "null" adds `| None`. Values keep their first-seen order and dedupe by
    (type, value), so `1` and `True` stay apart. Returns None for any open
    branch, an unsafe value, or no values.
    """
    if "anyOf" in schema and "oneOf" in schema:
        return None
    branches = schema.get("anyOf", schema.get("oneOf"))
    if not isinstance(branches, list):
        return None
    t = schema.get("type")
    nullable = t == "null" or (isinstance(t, list) and "null" in t)
    vals: list = []
    seen: set = set()
    for b in branches:
        if not isinstance(b, dict):
            return None
        pinned = _closed_values(b)
        if pinned is None:
            if b.get("type") == "null":
                nullable = True
                continue
            return None
        if not _literal_safe(pinned):
            return None
        for v in pinned:
            if v is None:
                nullable = True
            elif (type(v), v) not in seen:
                seen.add((type(v), v))
                vals.append(v)
    if not vals:
        return None
    return f"{_literal(vals)} | None" if nullable else _literal(vals)


def py_type(schema: dict | None) -> str:
    """Map a JSON Schema fragment to a Python type annotation string."""
    if not schema or not isinstance(schema, dict):
        return "Any"

    # Unions: an anyOf/oneOf of const/enum (and null) branches is a closed value
    # set and becomes Literal[...]; anything else, and every allOf, punts to Any
    # rather than guess wrong.
    if "allOf" not in schema and ("anyOf" in schema or "oneOf" in schema):
        lit = _union_literal(schema)
        if lit is not None:
            return lit
    if any(k in schema for k in ("anyOf", "oneOf", "allOf")):
        return "Any"

    # A lone non-null const pins one value, whatever `type` says.
    if "const" in schema and schema["const"] is not None and _literal_safe([schema["const"]]):
        return _literal([schema["const"]])

    # Enum: emit Literal[...] only when every member is a PEP 586-safe scalar.
    # Otherwise fall through to the type-based path so we emit the declared
    # type (or Any) instead.
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals and _literal_safe(enum_vals):
        t = schema.get("type")
        nullable = isinstance(t, list) and "null" in t
        return f"{_literal(enum_vals)} | None" if nullable else _literal(enum_vals)

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


# ── RFC 6570 URI templates ───────────────────────────────────────────────────
#
# Levels 1-4 for scalar values only: lists and associative arrays are out of
# scope, so the explode modifier `*` leaves a scalar unchanged (section 3.2.1).
# Literals are validated for braces only; any other character outside
# unreserved/reserved is pct-encoded on expansion (section 3.1) instead of
# rejected, because server templates are not always strictly conformant.
# The prefix modifier counts code points of the raw value, so it can split a
# pct-triplet already present in a `+`/`#` value.
# Expansion itself lives in the emitted `_EXPAND_URI` source: generated
# modules and `expand_uri_template` run the same code.


class UriTemplateError(ValueError):
    """A URI template whose expressions do not conform to RFC 6570 section 2."""


@dataclass(frozen=True)
class UriVar:
    """One varspec of a template expression, keyed by its raw (unsanitized) name."""

    name: str
    operator: str = ""
    prefix: int | None = None
    explode: bool = False

    @property
    def required(self) -> bool:
        """Form-style query variables (`?`, `&`) are optional; all others are required."""
        return self.operator not in ("?", "&")


_URI_OPERATORS = "+#./;?&"  # section 2.2 operator characters (level 2 and 3)
_URI_OP_RESERVED = "=,!@|"  # section 2.2: reserved for future extensions
_URI_VARCHAR = r"(?:[A-Za-z0-9_]|%[0-9A-Fa-f]{2})"
# varname, then either a prefix ":" %x31-39 0*3DIGIT or an explode "*" (section 2.4).
_URI_VARSPEC_RE = re.compile(rf"({_URI_VARCHAR}(?:\.?{_URI_VARCHAR})*)(?::([1-9][0-9]{{0,3}})|(\*))?")


def _parse_uri_expression(body: str, template: str) -> tuple[UriVar, ...]:
    if not body:
        raise UriTemplateError(f"empty expression in URI template {template!r}")
    if body[0] in _URI_OP_RESERVED:
        raise UriTemplateError(f"reserved operator {body[0]!r} in URI template {template!r}")
    op = body[0] if body[0] in _URI_OPERATORS else ""
    out: list[UriVar] = []
    for spec in body[len(op) :].split(","):
        m = _URI_VARSPEC_RE.fullmatch(spec)
        if not m:
            raise UriTemplateError(f"invalid varspec {spec!r} in URI template {template!r}")
        out.append(UriVar(m.group(1), op, int(m.group(2)) if m.group(2) else None, bool(m.group(3))))
    return tuple(out)


def parse_uri_template(template: str) -> list[str | tuple[UriVar, ...]]:
    """Split *template* into literal strings and expressions (tuples of `UriVar`).

    Raises `UriTemplateError` on an unbalanced or nested brace, an empty
    expression, a reserved operator or an invalid varspec.
    """
    parts: list[str | tuple[UriVar, ...]] = []
    pos = 0
    while pos < len(template):
        start = template.find("{", pos)
        literal = template[pos:] if start < 0 else template[pos:start]
        if "}" in literal:
            raise UriTemplateError(f"unmatched '}}' in URI template {template!r}")
        if literal:
            parts.append(literal)
        if start < 0:
            break
        end = template.find("}", start)
        if end < 0:
            raise UriTemplateError(f"unclosed '{{' in URI template {template!r}")
        body = template[start + 1 : end]
        if "{" in body:
            raise UriTemplateError(f"nested '{{' in URI template {template!r}")
        parts.append(_parse_uri_expression(body, template))
        pos = end + 1
    return parts


def uri_template_variables(template: str) -> list[tuple[str, bool]]:
    """Raw variable names in first-use order, each with whether it is required.

    A variable used more than once is required if any use is outside `?`/`&`.
    """
    seen: dict[str, bool] = {}
    for part in parse_uri_template(template):
        if isinstance(part, str):
            continue
        for var in part:
            seen[var.name] = seen.get(var.name, False) or var.required
    return list(seen.items())


# Emitted once per module when any resource template accessor is rendered, so
# generated modules expand templates without importing mcpgen.codegen. It skips
# validation: codegen only emits accessors for templates `parse_uri_template`
# accepts, and `expand_uri_template` validates before delegating to it.
# `ops` is RFC 6570 appendix A: operator -> (first, sep, named, ifemp); `+`
# and `#` also allow reserved characters and pct-triplets.
_EXPAND_URI = '''def _expand_uri(template: str, values: dict[str, Any]) -> str:
    """Expand an RFC 6570 URI template (levels 1-4) with scalar values.

    A None value is undefined and is omitted (RFC 6570 section 3.2.1); a bool
    expands as true/false."""
    from urllib.parse import quote

    reserved = ":/?#[]@!$&'()*+,;="
    hexdigits = "0123456789abcdefABCDEF"
    ops = {
        "": ("", ",", False, ""),
        "+": ("", ",", False, ""),
        "#": ("#", ",", False, ""),
        ".": (".", ".", False, ""),
        "/": ("/", "/", False, ""),
        ";": (";", ";", True, ""),
        "?": ("?", "&", True, "="),
        "&": ("&", "&", True, "="),
    }

    def enc(text: str, allow_reserved: bool) -> str:
        if not allow_reserved:
            return quote(text, safe="")
        out, i = [], 0
        while i < len(text):
            if text[i] == "%" and i + 2 < len(text) and text[i + 1] in hexdigits and text[i + 2] in hexdigits:
                out.append(text[i : i + 3])
                i += 3
            else:
                out.append(quote(text[i], safe=reserved))
                i += 1
        return "".join(out)

    out, pos = [], 0
    while True:
        start = template.find("{", pos)
        if start < 0:
            out.append(enc(template[pos:], True))
            return "".join(out)
        end = template.find("}", start)
        out.append(enc(template[pos:start], True))
        body = template[start + 1 : end]
        op = body[0] if body[0] in "+#./;?&" else ""
        first, sep, named, ifemp = ops[op]
        allow = op in ("+", "#")
        items = []
        for spec in body[len(op) :].split(","):
            prefix = None
            if spec.endswith("*"):
                spec = spec[:-1]
            elif ":" in spec:
                spec, n = spec.split(":", 1)
                prefix = int(n)
            value = values.get(spec)
            if value is None:
                continue
            if isinstance(value, bool):
                text = "true" if value else "false"
            else:
                text = str(value)
            if prefix is not None:
                text = text[:prefix]
            if named:
                items.append(spec + ("=" + enc(text, allow) if text else ifemp))
            else:
                items.append(enc(text, allow))
        if items:
            out.append(first + sep.join(items))
        pos = end + 1'''

_EXPAND_URI_NS: dict[str, Any] = {"Any": Any}
exec(compile(_EXPAND_URI, "<mcpgen.codegen._EXPAND_URI>", "exec"), _EXPAND_URI_NS)


def expand_uri_template(template: str, values: Mapping[str, Any]) -> str:
    """Expand *template* with scalar *values* keyed by raw variable name.

    A missing or `None` value is undefined and is omitted per section 3.2.1; a
    bool expands as `true`/`false`, any other value as `str(value)`. Raises
    `UriTemplateError` for a malformed template.
    """
    parse_uri_template(template)
    return _EXPAND_URI_NS["_expand_uri"](template, values)


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


def _docstring(text: str | None, indent: str, indent_blank_lines: bool = True) -> str:
    """Docstring source for *text*. `indent_blank_lines=False` leaves blank and
    whitespace-only lines empty and strips trailing whitespace from the rest;
    tool wrappers keep the indented form so their output stays byte-stable."""
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
        if indent_blank_lines:
            body = f"\n{indent}".join(lines)
        else:
            body = lines[0].rstrip() + "".join(f"\n{indent}{ln.rstrip()}" if ln.strip() else "\n" for ln in lines[1:])
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
    disc_required = disc in required

    ordered = sorted(props.items(), key=lambda kv: kv[0] not in required)
    coerced_variants = sorted(((_variant_key(k, disc, numeric), v) for k, v in variants.items()), key=lambda kv: kv[0])

    def _build_params(disc_type: str | None) -> list[str]:
        """Render the parameter list; `disc_type=None` omits the discriminator entirely.

        The discriminator carries no default even when the schema marks it optional,
        because each @overload pins it to one `Literal` value. Omission is expressed
        as its own overload (see `disc_required`), not as a default on this one.
        """
        out = []
        for pname, pschema in ordered:
            py = sanitize(pname)
            ann = overrides.get(pname) or py_type(pschema)
            if pname == disc:
                if disc_type is not None:
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

    variant_models = [(val, v.get("return_model", "Any")) for val, v in coerced_variants if v.get("return_model")]
    union_ret = " | ".join(m for _, m in variant_models) if variant_models else "Any"

    blocks: list[str] = []

    for val, model in variant_models:
        ret = f"list[{model}]" if container == "list" else model
        literal = str(val) if numeric else repr(val)
        params = _build_params(f"Literal[{literal}]")
        blocks.append(f"@overload\nasync def {fn}({_sig(params)}) -> {ret}: ...")

    impl_ret = f"list[{union_ret}]" if container == "list" else union_ret

    # A discriminator the schema marks optional may be omitted, and the server then
    # picks a variant. Which one is not knowable from the schema, so the omission
    # overload returns the union rather than guessing.
    if not disc_required:
        blocks.append(f"@overload\nasync def {fn}({_sig(_build_params(None))}) -> {impl_ret}: ...")

    impl_disc = disc_type if disc_required else f"{disc_type} | None = None"
    impl_lines = [f"async def {fn}({_sig(_build_params(impl_disc))}) -> {impl_ret}:"]
    if embed_schema:
        impl_lines.append(_docstring_with_args(tool, ordered, "    ", shape=shape))
    else:
        impl_lines.append(_docstring(_with_size_note(tool.get("description"), shape), "    "))

    body_args = [
        (sanitize(pname), pname, pname in required or (pname == disc and disc_required)) for pname, _ in ordered
    ]
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


def _return_annotation(shape: dict) -> str:
    """Return annotation for a flat (non-discriminated) wrapper or resource accessor.

    return_container="list" => the unwrapped value is a LIST of return_model
    records (e.g. query_acme's data.results), so annotation/cast become
    list[Model] and the body digs via _dig_list. Default (None) = dict/scalar.
    """
    return_model: str | None = shape.get("return_model")
    if not return_model:
        return "Any"
    return f"list[{return_model}]" if shape.get("return_container") == "list" else return_model


def _return_lines(call: str, shape: dict, ret_ann: str) -> list[str]:
    """Body lines that await *call* and return it, unwrapped and cast per *shape*."""
    unwrap: list = shape.get("unwrap") or []
    return_model: str | None = shape.get("return_model")
    if unwrap:
        path = "(" + "".join(f"{k!r}, " for k in unwrap) + ")"
        digger = "_dig_list" if shape.get("return_container") == "list" else "_dig"
        dug = f"{digger}(result, {path})"
        ret = f'cast("{ret_ann}", {dug})' if return_model else dug
        return [f"    result = {call}", f"    return {ret}"]
    if return_model:
        return [f'    return cast("{ret_ann}", {call})']
    return [f"    return {call}"]


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

    overrides: dict = _get_overrides(shape)
    ret_ann = _return_annotation(shape)

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
    lines.extend(_return_lines(call, shape, ret_ann))

    result = "\n".join(lines)
    if embed_schema:
        result += f"\n\n{fn}.__schema__ = {repr(schema)}"
    return result


# ── Resource accessors ───────────────────────────────────────────────────────
#
# Resources arrive as MCP `Resource` / `ResourceTemplate` dicts (`uri` or
# `uriTemplate`, plus optional `name`, `description`, `mimeType`) with URIs
# already normalized by the caller. Shape-spec entries are keyed by that URI or
# template string, never by `name`.

# Top-level names every generated module may define; an accessor never takes one.
_MODULE_NAMES = frozenset({"SERVER", "_dig", "_dig_list", "_expand_uri"})
# Names an accessor body references; a template variable that sanitizes to one
# of them gets a trailing underscore so the parameter cannot shadow it.
_ACCESSOR_RESERVED = frozenset({"caller", "cast", "uri", *_MODULE_NAMES})
_RESOURCE_KINDS = ("resource", "resource_template")


def _is_resource_entry(key: str, entry: Any, tool_names: set[str]) -> bool:
    """Whether a shapes.json entry belongs to a resource rather than a tool.

    A key naming a rendered tool is always a tool entry. Otherwise `kind`
    decides, falling back to a `:` in the key (MCP tool names should not
    contain one; URIs always do).
    """
    if key in tool_names:
        return False
    return (isinstance(entry, dict) and entry.get("kind") in _RESOURCE_KINDS) or ":" in key


def _resource_shape(shapes: dict, kind: str, key: str) -> dict:
    """The shapes.json entry for a resource of *kind* keyed by *key*, or `{}`.

    An entry tagged with the other kind is skipped, so a static URI and a
    template spelled the same never share one; an untagged entry applies to both.
    """
    entry = shapes.get(key)
    if isinstance(entry, dict) and entry.get("kind") in (None, kind):
        return entry
    return {}


def _resource_base_name(uri: str) -> str:
    """`read_` + the URI with template expressions dropped and non-alphanumeric runs collapsed to `_`."""
    stem = re.sub(r"[^0-9A-Za-z]+", "_", re.sub(r"\{[^}]*\}", "", uri)).strip("_")
    return f"read_{stem}" if stem else "read"


def resource_function_names(
    tools: list[dict], resources: list[dict] | None = None, templates: list[dict] | None = None
) -> dict[tuple[str, str], str]:
    """Map each `("resource", uri)` and `("resource_template", template)` to its accessor name.

    Keys pair the shape-spec `kind` with the URI or template string, so a static
    URI and a template spelled the same get distinct names. Statics are named
    first in URI order, then templates in template order. A name already taken
    by a sanitized tool name, a module-level name or an earlier accessor gets a
    `_2`, `_3`, … suffix.
    """
    taken = {sanitize(t["name"]) for t in tools} | _MODULE_NAMES
    keys = [("resource", u) for u in sorted({r["uri"] for r in resources or []})] + [
        ("resource_template", t) for t in sorted({t["uriTemplate"] for t in templates or []})
    ]
    names: dict[tuple[str, str], str] = {}
    for key in keys:
        base = fn = _resource_base_name(key[1])
        n = 2
        while fn in taken:
            fn = f"{base}_{n}"
            n += 1
        taken.add(fn)
        names[key] = fn
    return names


def _resource_doc(label: str, key: str, meta: dict, shape: dict) -> str | None:
    """Description, then the stable `<label>: <key>` line the runner skill greps for, then the mimeType."""
    ref = f"{label}: {key}"
    if meta.get("mimeType"):
        ref += f"\nMIME type: {meta['mimeType']}"
    desc = (meta.get("description") or "").strip()
    return _with_size_note(f"{desc}\n\n{ref}" if desc else ref, shape)


def _render_accessor(
    fn: str, label: str, key: str, meta: dict, shape: dict | None, params: list[str], pre: list[str], uri_expr: str
) -> str:
    """Shared body of `render_resource` / `render_resource_template`; returns are handled as in `render_tool`."""
    shape = shape or {}
    if shape.get("discriminator") or shape.get("variants"):
        print(
            f"[codegen] ⚠  discriminator/variants ignored for resource {key!r} — resources take no arguments to discriminate on",
            file=sys.stderr,
        )
    ret_ann = _return_annotation(shape)
    sig = ", ".join(["caller: McpResourceReader", "*", *params] if params else ["caller: McpResourceReader"])
    doc = _docstring(_resource_doc(label, key, meta, shape), "    ", indent_blank_lines=False)
    lines = [f"async def {fn}({sig}) -> {ret_ann}:", doc, *pre]
    lines.extend(_return_lines(f"await caller.read_resource(SERVER, {uri_expr})", shape, ret_ann))
    return "\n".join(lines)


def render_resource(resource: dict, fn: str | None = None, shape: dict | None = None) -> str:
    """Render a static resource into an `async def` against the McpResourceReader seam.

    `fn` defaults to the URI-derived name; `render_module` passes the
    de-duplicated one from `resource_function_names`. `shape` is the entry keyed
    by the URI and supports `unwrap` / `return_model` / `return_container` /
    `fields` exactly as for tools.
    """
    uri = resource["uri"]
    return _render_accessor(fn or _resource_base_name(uri), "Resource", uri, resource, shape, [], [], _str_literal(uri))


def render_resource_template(template: dict, fn: str | None = None, shape: dict | None = None) -> str:
    """Render a resource template into an accessor taking its variables as keyword-only arguments.

    Variables are `str` unless `input_overrides` (keyed by the raw variable
    name) says otherwise; `?`/`&` variables are optional. The values dict keeps
    the raw names so `_expand_uri` resolves them. Raises `UriTemplateError` for
    a malformed template.
    """
    tpl = template["uriTemplate"]
    variables = uri_template_variables(tpl)
    overrides = _get_overrides(shape or {})
    required: list[str] = []
    optional: list[str] = []
    pairs: list[str] = []
    used: set[str] = set()
    for raw, is_required in variables:
        py = sanitize(raw)
        while py in _ACCESSOR_RESERVED or py in used:
            py += "_"
        used.add(py)
        ann = overrides.get(raw) or "str"
        if is_required:
            required.append(f"{py}: {ann}")
        else:
            opt_ann = ann if ann.endswith("| None") else f"{ann} | None"
            optional.append(f"{py}: {opt_ann} = None")
        pairs.append(f"{_str_literal(raw)}: {py}")
    pre = [f"    uri = _expand_uri({_str_literal(tpl)}, {{{', '.join(pairs)}}})"]
    return _render_accessor(
        fn or _resource_base_name(tpl), "Resource template", tpl, template, shape, required + optional, pre, "uri"
    )


_HEADER = '''"""Generated MCP wrappers for the {server!r} server. DO NOT hand-edit signatures.

{about}
{probe_note}"""
from __future__ import annotations

{imports}

from mcpgen.seam import {seam}

SERVER = {server!r}
'''


# Header paragraph per module content: tools only, tools + resources, resources only.
_ABOUT_TOOLS = """Generated by mcpgen from a live tools/list. Each function forwards to an
injected McpCaller (see mcpgen.seam); the auth/transport backend is the
caller's concern, not this module's."""
_ABOUT_TOOLS_AND_RESOURCES = """Generated by mcpgen from a live tools/list and resources/list. Tool wrappers
forward to an injected McpCaller and resource accessors to an injected
McpResourceReader (see mcpgen.seam); the auth/transport backend is the
caller's concern, not this module's."""
_ABOUT_RESOURCES = """Generated by mcpgen from a live tools/list and resources/list. Resource
accessors forward to an injected McpResourceReader (see mcpgen.seam); the
auth/transport backend is the caller's concern, not this module's."""


_PY_BUILTINS = frozenset({"str", "int", "float", "list", "dict", "bool", "bytes", "object", "type", "set", "tuple"})


def _warn_unmatched_resource_entries(
    shapes: dict, tool_names: set[str], resources: list[dict] | None, templates: list[dict] | None
) -> None:
    """Warn for each resource shapes entry that no listed resource or template picks up.

    Catches a probe keyed by a template that differs from the listed one
    (`demo://records/{id}` vs `demo://records/{record_id}`), whose entry would
    otherwise be dropped silently. An entry tagged with a `kind` is checked
    against that kind's listing, an untagged one against both, as
    `_resource_shape` applies it to either. A listing of `None` was not
    collected, so an entry that could belong to it is not judged. An entry
    whose `kind` is neither `resource` nor `resource_template` (a misspelling
    such as `resource-template`) applies to nothing and always warns, unless
    its key names a tool, whose wrapper ignores `kind`.
    """
    listed = {
        "resource": None if resources is None else {r["uri"] for r in resources},
        "resource_template": None if templates is None else {t["uriTemplate"] for t in templates},
    }
    for key, entry in shapes.items():
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if kind is not None and kind not in _RESOURCE_KINDS and key not in tool_names:
            print(f"[codegen] ⚠  shapes entry {key!r} has unknown kind {kind!r} — not applied", file=sys.stderr)
            continue
        if not _is_resource_entry(key, entry, tool_names):
            continue
        kinds = [kind] if kind in _RESOURCE_KINDS else list(_RESOURCE_KINDS)
        keys = [listed[k] for k in kinds]
        if any(k is None for k in keys) or any(key in k for k in keys if k is not None):
            continue
        label = " or ".join(k.replace("_", " ") for k in kinds)
        print(f"[codegen] ⚠  shapes entry {key!r} matches no listed {label} — not applied", file=sys.stderr)


def _valid_templates(templates: list[dict]) -> list[dict]:
    """Templates that parse, first occurrence per template string; each malformed one warns and is skipped."""
    out: dict[str, dict] = {}
    for t in templates:
        tpl = t["uriTemplate"]
        try:
            parse_uri_template(tpl)
        except UriTemplateError as e:
            print(f"[codegen] ⚠  resource template {tpl!r} skipped: {e}", file=sys.stderr)
            continue
        out.setdefault(tpl, t)
    return [out[k] for k in sorted(out)]


# `typing` names a module body may reference. `Any` is always imported; the
# rest only when the rendered code uses them.
_TYPING_OPTIONAL = ("Literal", "TypedDict", "cast", "overload")


def _names(node: ast.AST, skip: frozenset[str] = frozenset()) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id not in skip}


def _module_names(tree: ast.Module) -> set[str]:
    """Bare names *tree* reads from module scope.

    A function body's reference to one of its own parameters is local: a tool
    param named `cast` renders `args["cast"] = cast`, which needs no import.
    Decorators, annotations and defaults resolve in module scope and always
    count. A body that shadows a name it also calls (a `cast` param on a
    wrapper that returns `cast(...)`) is broken either way and not handled.
    """
    used: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = frozenset(a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg))
            for h in [*node.decorator_list, node.args, *([node.returns] if node.returns else [])]:
                used |= _names(h)
            for stmt in node.body:
                used |= _names(stmt, params)
        else:
            used |= _names(node)
    return used


def _typing_imports(body: str) -> str:
    """The `from typing import ...` line for a rendered module *body*.

    A name counts when the body reads it from module scope as a bare name
    (see `_module_names`), so docstrings and `typing.Literal[...]` do not.
    Hand-edited annotations can make the body unparsable, and a lone surrogate
    in a description makes it unencodable; a whole-word match then errs on
    importing.
    """
    try:
        used = _module_names(ast.parse(body))
    except (SyntaxError, ValueError):  # UnicodeEncodeError is a ValueError
        used = {n for n in _TYPING_OPTIONAL if re.search(rf"\b{n}\b", body)}
    return "from typing import " + ", ".join(["Any", *(n for n in _TYPING_OPTIONAL if n in used)])


def render_module(
    server: str,
    tools: list[dict],
    shapes: dict | None = None,
    probe_note: str = "",
    embed_schema: bool = False,
    resources: list[dict] | None = None,
    resource_templates: list[dict] | None = None,
) -> str:
    """Render a full wrapper module.

    `shapes` maps tool name, resource URI or template string -> probe-derived
    shape-spec (see render_tool). When present, the module gains TypedDict
    return models and a `_dig` unwrap helper; the no-shapes path is
    byte-identical to pure stub generation. Resource accessors follow the tools,
    statics before templates; without resources the output is byte-identical to
    a tools-only module. A resource entry in `shapes` that matches no listed
    resource or template warns on stderr.
    """
    shapes = shapes or {}
    note = f"\n{probe_note}\n" if probe_note else ""
    statics: dict[str, dict] = {}
    for r in resources or []:
        statics.setdefault(r["uri"], r)
    static_list = [statics[k] for k in sorted(statics)]
    template_list = _valid_templates(resource_templates or [])
    resource_names = resource_function_names(tools, static_list, template_list)
    static_specs = [_resource_shape(shapes, "resource", r["uri"]) for r in static_list]
    template_specs = [_resource_shape(shapes, "resource_template", t["uriTemplate"]) for t in template_list]
    resource_specs = static_specs + template_specs
    tool_names = {t["name"] for t in tools}
    _warn_unmatched_resource_entries(shapes, tool_names, resources, resource_templates)
    # A resource entry only counts when rendered, via resource_specs.
    needs_json = bool(shapes) and any(
        s.get("unwrap") for s in [*(shapes.get(t["name"]) or {} for t in tools), *resource_specs]
    )
    # Everything below the header; the header's typing import is derived from it.
    parts: list[str] = []

    # Specs whose return_model was renamed by a collision; the renderers read
    # return_model from the spec they are handed, so they get these copies.
    # The caller's `shapes` is never mutated.
    resolved_tool_shapes: dict[str, dict] = {}
    if shapes:
        models: list[str] = []
        _seen_models: dict[str, str] = {}  # emitted name → rendered body; dedup across tools and resources

        def _append_model(name: str, fields: dict, owner: str) -> str:
            """Emit the model unless an identical one exists; return the class name the owner must annotate.

            Same name with different fields gets the first `_2`, `_3`, … suffix
            whose body matches, or else the first free one.
            """
            if name in _PY_BUILTINS:
                print(
                    f"[codegen] ⚠  return_model {name!r} is a Python builtin — ignored; use null instead",
                    file=sys.stderr,
                )
                return name
            candidate, suffix = name, 1
            while candidate in _seen_models:
                if _seen_models[candidate] == render_model(candidate, fields):
                    return candidate
                suffix += 1
                candidate = f"{name}_{suffix}"
            body = render_model(candidate, fields)
            _seen_models[candidate] = body
            models.append(body)
            if candidate != name:
                print(
                    f"[codegen] ⚠  shape collision for {name!r} ({owner}); emitted {candidate!r}",
                    file=sys.stderr,
                )
            return candidate

        def _resolve_flat(sp: dict, owner: str) -> dict:
            if not sp.get("return_model"):
                return sp
            got = _append_model(sp["return_model"], sp.get("fields") or {}, owner)
            return sp if got == sp["return_model"] else {**sp, "return_model": got}

        for tool in sorted(tools, key=lambda t: t["name"]):
            sp = shapes.get(tool["name"])
            if not sp:
                continue
            if sp.get("discriminator") and sp.get("variants"):
                variants = sp["variants"]
                disc, numeric = _discriminator_context(tool, sp)
                renamed: dict = {}
                for key, variant in sorted(variants.items(), key=lambda kv: _variant_key(kv[0], disc, numeric)):
                    resolved = _resolve_flat(variant, f"{tool['name']}[{key}]")
                    if resolved is not variant:
                        renamed[key] = resolved
                if renamed:
                    resolved_tool_shapes[tool["name"]] = {**sp, "variants": {**variants, **renamed}}
            else:
                resolved = _resolve_flat(sp, tool["name"])
                if resolved is not sp:
                    resolved_tool_shapes[tool["name"]] = resolved
        static_specs = [_resolve_flat(sp, r["uri"]) for r, sp in zip(static_list, static_specs, strict=True)]
        template_specs = [
            _resolve_flat(sp, t["uriTemplate"]) for t, sp in zip(template_list, template_specs, strict=True)
        ]
        resource_specs = static_specs + template_specs
        parts.extend(models)
        specs = [shapes.get(t["name"]) or {} for t in tools] + resource_specs
        if any(s.get("unwrap") and s.get("return_container") != "list" for s in specs):
            parts.append(_DIG)
        if any(s.get("unwrap") and s.get("return_container") == "list" for s in specs):
            parts.append(_DIG_LIST)
    if template_list:
        parts.append(_EXPAND_URI)

    for tool in sorted(tools, key=lambda t: t["name"]):
        shape = resolved_tool_shapes.get(tool["name"]) or shapes.get(tool["name"])
        parts.append(render_tool(tool, shape, embed_schema=embed_schema))
    for r, spec in zip(static_list, static_specs, strict=True):
        parts.append(render_resource(r, resource_names["resource", r["uri"]], spec))
    for t, spec in zip(template_list, template_specs, strict=True):
        parts.append(render_resource_template(t, resource_names["resource_template", t["uriTemplate"]], spec))

    body = "\n\n\n".join(parts)
    type_imports = _typing_imports(body)
    imports = ("import json\n" + type_imports) if needs_json else type_imports
    if not resource_names:
        seam, about = "McpCaller", _ABOUT_TOOLS
    elif tools:
        seam, about = "McpCaller, McpResourceReader", _ABOUT_TOOLS_AND_RESOURCES
    else:
        seam, about = "McpResourceReader", _ABOUT_RESOURCES
    header = _HEADER.format(server=server, about=about, probe_note=note, imports=imports, seam=seam)
    return "\n\n\n".join([header, *parts]) + "\n"


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
    tool: str,
    args_list: list[dict],
    shapes: list[Any],
    observed_bytes: list[int] | None = None,
    kind: str | None = None,
) -> dict:
    """Build a shape-spec skeleton from N probe calls.

    tool           — the entry key: a tool name, resource URI or template string.
    args_list      — the N arg-dicts passed to the tool (one per probe).
    shapes         — the N summarize_shape() results from those calls.
    observed_bytes — optional raw response sizes (one per probe); the largest
                     is recorded as `_observed_bytes` so the generated docstring
                     can warn callers about outsized payloads. Omitted entirely
                     when not passed (byte-stable with existing shapes.json).
    kind           — `"resource"` / `"resource_template"` for a resource entry.
                     Omitted when not passed, so tool entries stay byte-stable.

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
    entry: dict[str, Any] = {"kind": kind} if kind is not None else {}
    entry |= {
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


# --- probed_args scrubbing -------------------------------------------------
#
# `probed_args` is captured verbatim from live calls, and <server>.shapes.json
# is committed.  Real ids in a version-controlled file survive deletion (git
# history) and travel to everyone the repo reaches, so the CLI scrubs before
# writing that file; the raw values stay in the gitignored .parts/ and
# <stem>.verify.json, which the roundtrip verifier reads first.
#
# Strings only.  Non-string scalars are left alone deliberately: replacing an
# int account id with a placeholder string would change the JSON type and
# mislead both `input_overrides` and the verifier.  The placeholders below
# match none of the patterns, so scrubbing is idempotent.

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# No \b: a UUID glued to a word character ("id_3f25…") is still a UUID, but \b
# fails against `_` and letters.  Hex look-around instead, so only a longer hex
# run is excluded.
_UUID_RE = re.compile(
    r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])"
)
# Anchored at string start: an unanchored /home/ or /Users/ segment also occurs
# inside URLs and remote paths ("https://example.com/home/page"), and rewriting
# those would corrupt functional values on their way into a committed file.
# The optional file:// prefix matters: file:///Users/<name>/x is a standard arg
# form for filesystem-shaped servers, the very class this rule was written for.
# The Windows branch is separator- and case-tolerant because C:/Users/<name> and
# c:\\users\\<name> are both accepted spellings.
_POSIX_HOME_RE = re.compile(r"^(?:file://)?(?:/Users|/home)/[^/\\]+")
_WINDOWS_HOME_RE = re.compile(r"^(?:file:///)?[A-Za-z]:[\\/][Uu]sers[\\/][^\\/]+")
_LONG_ID_RE = re.compile(r"\b\d{8,}\b")


def _scrub_str(value: str) -> str:
    """Replace PII-shaped substrings in one string value."""
    out = _EMAIL_RE.sub("<email>", value)
    out = _UUID_RE.sub("<uuid>", out)
    out = _POSIX_HOME_RE.sub("<home>", out, count=1)
    out = _WINDOWS_HOME_RE.sub("<home>", out, count=1)
    out = _LONG_ID_RE.sub("<id>", out)
    return out


def scrub_probed_args(value: Any) -> tuple[Any, bool]:
    """Return `(scrubbed_copy, changed)` for a probed_args value.

    Recurses through dicts and lists; scrubs string *values* only — never dict
    keys, never non-string scalars.  Pure: the input is not mutated.
    """
    if isinstance(value, str):
        out = _scrub_str(value)
        return out, out != value
    if isinstance(value, dict):
        changed = False
        result: dict = {}
        for k, v in value.items():
            result[k], sub_changed = scrub_probed_args(v)
            changed = changed or sub_changed
        return result, changed
    if isinstance(value, list):
        changed = False
        items = []
        for v in value:
            scrubbed, sub_changed = scrub_probed_args(v)
            items.append(scrubbed)
            changed = changed or sub_changed
        return items, changed
    return value, False


def scrub_skeletons(spec: dict) -> tuple[dict, list[str]]:
    """Scrub `probed_args` across a whole shapes mapping.

    Returns `(scrubbed_copy, tools_changed)`.  Entries whose args were altered
    get `probe_args_scrubbed: True` — the flag the shape-spec already defines
    and the runner skill already reads.  Pure: `spec` is not mutated.
    """
    out: dict = {}
    touched: list[str] = []
    for tool, entry in spec.items():
        if not isinstance(entry, dict) or "probed_args" not in entry:
            out[tool] = entry
            continue
        new_entry = dict(entry)
        new_entry["probed_args"], changed = scrub_probed_args(entry["probed_args"])
        if changed:
            new_entry["probe_args_scrubbed"] = True
            touched.append(tool)
        out[tool] = new_entry
    return out, sorted(touched)
