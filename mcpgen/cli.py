"""mcpgen CLI: generate typed wrappers from a live MCP server.

    mcpgen codegen acme --out acme.py
    mcpgen codegen acme --probe whoami --probe-args '{}'

Deterministic stub generation is the 80%; the optional --probe adds one live call
and records the observed response *shape* (not payload) — the empirical step that
distinguishes this from pure inputSchema codegen.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote

import anyio
from mcp.shared.exceptions import McpError
from mcp.types import ReadResourceResult, TextResourceContents

from . import _bridge, codegen, discovery
from . import manifest as manifest_mod

if sys.version_info < (3, 11):  # pragma: no cover - version-gated
    from exceptiongroup import BaseExceptionGroup


async def _list_tools(
    server: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
    env: dict[str, str] | None = None,
    on_session: Callable[[Any], Awaitable[None]] | None = None,
) -> list[dict]:
    """List the server's tools as plain dicts.

    *on_session*, when given, is awaited with the initialized session after
    ``tools/list``, before the session closes, so a caller can issue further
    list requests over the same connection and OAuth handshake.
    """
    async with _bridge.session(
        server,
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        creds_path=creds_path,
        env=env,
    ) as s:
        result = await s.list_tools()
        if on_session is not None:
            await on_session(s)
    tools = []
    for t in result.tools:
        raw_annotations = getattr(t, "annotations", None)
        annotations = raw_annotations.model_dump(exclude_none=True) if raw_annotations else None
        tools.append(
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema,
                "annotations": annotations,
            }
        )
    return tools


INVENTORY_SCOPES = ("all", "templates", "none")

PREVIEW_TIMEOUT = 10.0
"""Seconds a non-strict ``_list_inventory`` waits for its resource requests.

Bounds the ``resources/list`` / ``resources/templates/list`` round trips only:
opening the session, ``tools/list`` and closing the session (on HTTP, a DELETE
under httpx's own 30s timeout) are outside it.
"""


class ResourceListingFailed(Exception):
    """The session failed under a strict ``_list_inventory`` while resources were being listed.

    Raised for a transport failure outside the hook, such as an HTTP 502 on
    ``resources/list`` failing the transport's per-request task, which would
    otherwise escape as an ExceptionGroup. The message is already
    ``_bridge._describe``d; the original is chained as ``__cause__``.
    """


@dataclass
class Inventory:
    """A server's tools plus, when collected, its resources and resource templates.

    ``None`` means "not collected" (manifest: untracked, never compared); ``[]``
    means collected and empty. The remaining fields are set only by a non-strict
    ``_list_inventory``: ``*_truncated`` when the server offered pages beyond the
    first, and ``resource_error`` when the resource listing failed or timed out.
    """

    tools: list[dict]
    resources: list[dict] | None = None
    templates: list[dict] | None = None
    resources_truncated: bool = False
    templates_truncated: bool = False
    resource_error: BaseException | None = None


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaves(inner)]
    return [exc]


def _auth_leaf(exc: BaseException) -> BaseException | None:
    """The first leaf of *exc* when every leaf is ``ReauthenticationRequired`` / ``LoginWontHelp``."""
    leaves = _leaves(exc)
    if all(isinstance(leaf, (_bridge.ReauthenticationRequired, _bridge.LoginWontHelp)) for leaf in leaves):
        return leaves[0]
    return None


def _check_reportable(group: BaseExceptionGroup) -> None:
    """Return when *group*, escaping a one-shot session, is an error to report; raise otherwise.

    A session that fails while opening (a stdio server crashing on startup, a
    failed HTTP ``initialize``) or in a transport task (``ReauthenticationRequired``
    from a mid-request 401) surfaces as the transport's ExceptionGroup, never
    as the bare error. An interrupt re-raises the group unchanged. An auth-only
    group raises its leaf for ``main()``'s auth handler. A group of nothing but
    ``McpError`` / ``ValueError`` returns: the caller prints
    ``_bridge._describe(group)`` and exits 1. Anything else is a bug and
    re-raises the group, to traceback.
    """
    if _bridge._carries_interrupt(group):
        raise group
    auth = _auth_leaf(group)
    if auth is not None:
        raise auth from group
    if not all(isinstance(leaf, (McpError, ValueError)) for leaf in _leaves(group)):
        raise group


async def _list_inventory(server: str, *, scope: str, strict: bool = True, **conn: Any) -> Inventory:
    """List tools and, per *scope*, resources/templates over one session.

    *scope* is ``all`` (resources and templates), ``templates`` (templates only:
    no ``resources/list`` is sent and ``resources`` stays ``None``) or ``none``
    (no resource requests).

    Goes through the module-global ``_list_tools``, so a test that patches it
    never runs the hook and gets ``resources=None, templates=None``.

    Strict (the default) lists every page. Non-strict is for a hint and must
    never cost the tools listing: it reads only the first page of each list and
    gives up on the resource requests after ``PREVIEW_TIMEOUT`` seconds.

    How a resource failure surfaces depends on where it happens:

    - ``McpError`` / ``ValueError`` from the listing (an error response,
      pydantic validation, a pagination fault) is caught inside the hook. Strict
      re-raises it after the session closes, so it surfaces as itself rather
      than inside the transport's ExceptionGroup; non-strict records it on
      ``resource_error``.
    - A transport failure: the hook is cancelled from outside (an HTTP 502 fails
      the transport's per-request task, whose task group then cancels the
      session body) or sees ``anyio.BrokenResourceError`` /
      ``ClosedResourceError``. Strict raises ``ResourceListingFailed``;
      non-strict records it and lists the tools again in a fresh session
      without the hook.
    - If every leaf of the failure is ``ReauthenticationRequired`` /
      ``LoginWontHelp``, that leaf is raised in both modes, for ``main()``'s
      auth handler.
    - Anything else the hook raises is a bug and propagates, as do interrupts, a
      failure before the hook ran (a tools failure), and a failure while
      closing the session after the hook finished (which fails a tools-only
      listing just the same; the resources are not reported as unlisted).
    """
    if scope not in INVENTORY_SCOPES:
        raise ValueError(f"unknown resource scope {scope!r}; expected one of {', '.join(INVENTORY_SCOPES)}")
    inv = Inventory(tools=[])
    # How the hook ended: None (never ran), "finished", "raised", or "transport"
    # (cancelled from outside, or a broken/closed stream inside).
    hook_outcome: str | None = None

    async def _collect(s: Any) -> None:
        nonlocal hook_outcome
        hook_outcome = "raised"
        statics = scope == "all"
        try:
            if strict:
                resources, templates = await _bridge.list_resource_inventory(s, statics=statics)
            else:
                with anyio.move_on_after(PREVIEW_TIMEOUT) as deadline:
                    preview = await _bridge.preview_resource_inventory(s, statics=statics)
                if deadline.cancelled_caught:
                    inv.resource_error = TimeoutError(f"no answer within {PREVIEW_TIMEOUT:g}s")
                    hook_outcome = "finished"
                    return
                resources, inv.resources_truncated, templates, inv.templates_truncated = preview
        except (McpError, ValueError) as exc:
            inv.resource_error = exc
            hook_outcome = "finished"
            return
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.get_cancelled_exc_class()):
            # Our own deadline is absorbed by move_on_after above, so a
            # cancellation reaching here was requested from outside the hook.
            hook_outcome = "transport"
            raise
        inv.resources = resources if statics else None
        inv.templates = templates
        hook_outcome = "finished"

    try:
        inv.tools = await _list_tools(server, on_session=None if scope == "none" else _collect, **conn)
    except BaseException as exc:
        if hook_outcome is None or _bridge._carries_interrupt(exc):
            raise
        auth = _auth_leaf(exc)
        if auth is not None:
            raise auth from exc
        if hook_outcome != "transport":
            raise
        if strict:
            raise ResourceListingFailed(f"could not list resources: {_bridge._describe(exc)}") from exc
        inv = Inventory(tools=await _list_tools(server, on_session=None, **conn), resource_error=exc)
    if strict and inv.resource_error is not None:
        raise inv.resource_error
    return inv


async def _probe(
    server: str,
    tool: str,
    args: dict,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Any, int, Any]:
    """Return `(observed_shape, observed_byte_size, raw_payload)` for one live probe call.

    The raw payload is kept so `probe --save-raw` can persist it without a
    second live call against a rate-limited server (see cli `_cmd_probe`).
    """
    caller = _bridge.McpBridgeCaller(
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        creds_path=creds_path,
        env=env,
    )
    raw = await caller.call(server, tool, args)
    size = len(json.dumps(raw, default=str, ensure_ascii=False).encode("utf-8"))
    return codegen.summarize_shape(raw), size, raw


async def _probe_resource(
    server: str,
    uri: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Any, int, Any]:
    """`_probe` for a resource: `(observed_shape, observed_byte_size, raw_payload)` of one read.

    Reads through ``read_resource``, the path generated accessors take, so the
    shape is of the parsed value they return. A blob arrives as ``bytes``, which
    ``summarize_shape`` records as ``"bytes"`` and whose size is its length.
    """
    caller = _bridge.McpBridgeCaller(
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        creds_path=creds_path,
        env=env,
    )
    raw = await caller.read_resource(server, uri)
    if isinstance(raw, bytes):
        size = len(raw)
    else:
        size = len(json.dumps(raw, default=_raw_default, ensure_ascii=False).encode("utf-8"))
    return codegen.summarize_shape(raw), size, raw


def _raw_default(obj: Any) -> Any:
    """``json.dumps`` fallback for raw payloads: base64 for bytes, ``str`` for anything else."""
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode("ascii")
    return str(obj)


async def _call(
    server: str,
    tool: str,
    args: dict,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    caller = _bridge.McpBridgeCaller(
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        creds_path=creds_path,
        env=env,
    )
    return await caller.call(server, tool, args)


async def _read_resource(
    server: str,
    uri: str,
    *,
    cmd: str | None = None,
    url: str | None = None,
    bearer: str | None = None,
    client_name: str | None = None,
    config_path: str | None = None,
    cred_backend: str | None = None,
    creds_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> ReadResourceResult:
    """Read *uri* and return the unparsed result, for `read`'s per-content output."""
    caller = _bridge.McpBridgeCaller(
        cmd=cmd,
        url=url,
        bearer=bearer,
        client_name=client_name,
        config_path=config_path,
        cred_backend=cred_backend,
        creds_path=creds_path,
        env=env,
    )
    return await caller.read_resource_result(server, uri)


def _json_object_arg(raw: str) -> dict:
    """Parse one ``--args`` value, which must be a JSON object; raises ``ValueError``."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--args must be valid JSON ({exc})") from None
    if not isinstance(value, dict):
        raise ValueError(f"--args must be a JSON object, got {type(value).__name__}: {raw}")
    return value


def _static_args_error(target: str) -> ValueError:
    """The error for --args given with *target*, a static resource URI."""
    return ValueError(f"--args applies to a URI template only; {target!r} has no {{variable}}")


def _resource_uri(target: str, values: dict | None) -> str:
    """The normalized URI that `read` / `probe --resource` reads for *target*.

    A target containing ``{`` is a URI template, expanded with *values* keyed by
    raw variable name: every required variable must be given, no unknown one may
    be, and values must be scalars. A static URI takes no values: *values* is
    ``None`` unless ``--args`` was passed, and passing it is an error. Raises
    ``ValueError`` (``codegen.UriTemplateError`` for a malformed template).
    """
    if "{" not in target:
        if values is not None:
            raise _static_args_error(target)
        return _normalized_uri(target)
    variables = codegen.uri_template_variables(target)
    values = values or {}
    names = [name for name, _required in variables]
    unknown = sorted(set(values) - set(names))
    if unknown:
        raise ValueError(
            f"unknown variable(s) {', '.join(unknown)} for {target!r}; the template has: {', '.join(names)}"
        )
    missing = [name for name, required in variables if required and values.get(name) is None]
    if missing:
        raise ValueError(f"missing required variable(s) {', '.join(missing)} for {target!r}; pass them in --args")
    nested = sorted(name for name, value in values.items() if isinstance(value, (dict, list)))
    if nested:
        raise ValueError(f"variable(s) {', '.join(nested)} must be a string, number, boolean or null")
    return _normalized_uri(codegen.expand_uri_template(target, values))


def _normalized_uri(uri: str) -> str:
    try:
        return _bridge.normalize_resource_uri(uri)
    except ValueError:
        # pydantic's multi-line ValidationError text adds nothing for a CLI user.
        raise ValueError(f"not a valid resource URI: {uri!r}") from None


def _server_stem(server: str) -> str:
    """Return a filesystem-safe stem for a server identifier (name or URL)."""
    if server.startswith(("http://", "https://")):
        from urllib.parse import urlparse

        parsed = urlparse(server)
        # e.g. "mcp.deepwiki.com" → "mcp.deepwiki.com"
        stem = parsed.netloc or parsed.path.strip("/").replace("/", "_")
    else:
        stem = server
    return stem


def _parse_env(ns: argparse.Namespace) -> dict[str, str] | None:
    """Parse --env KEY[=VAL] flags into a dict for the stdio subprocess.

    ``--env KEY``      forwards $KEY from the current shell environment.
    ``--env KEY=VAL``  sets KEY to VAL inline (no shell lookup).

    Returns None when no --env flags were given (or all were skipped).
    Unset keys produce a stderr warning and are skipped rather than raising.
    """
    items: list[str] = getattr(ns, "env", None) or []
    result: dict[str, str] = {}
    for item in items:
        if "=" in item:
            k, v = item.split("=", 1)
            if not k:
                print(f"[mcpgen] ⚠  --env {item!r}: empty key name; skipped", file=sys.stderr)
                continue
            result[k] = v
        elif item in os.environ:
            result[item] = os.environ[item]
        else:
            print(
                f"[mcpgen] ⚠  --env {item}: not set in environment; skipped",
                file=sys.stderr,
            )
    return result or None


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically; see ``_atomic_write``."""
    _atomic_write(path, lambda tmp: tmp.write_text(text))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically and byte for byte; see ``_atomic_write``."""
    _atomic_write(path, lambda tmp: tmp.write_bytes(data))


def _atomic_write(path: Path, write: Callable[[Path], object]) -> None:
    """Stage *path* through *write* into a pid-unique temp file, then os.replace it.

    Safe on all OS (Windows, macOS, Linux) — os.replace is atomic when src and
    dst are on the same filesystem, which is always true here.  Using the pid in
    the temp name means concurrent probe processes for the *same* tool each get
    their own staging file; the last os.replace wins (fine — same-tool last-writer
    semantics are acceptable).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        write(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _parts_dir(target: Path) -> Path:
    """Return the parts directory for a shapes target path.

    e.g. acme.shapes.json  →  acme.shapes.json.parts/
    """
    return target.with_name(target.name + ".parts")


_PART_NAME_MAX = 200
"""Longest part file stem; leaves room for ``.json`` and ``_atomic_write``'s temp suffix under 255 bytes."""


def _part_filename(key: str) -> str:
    """The ``.parts/`` file name for the shapes entry *key*: a tool name, resource URI or template.

    The key is URL-quoted, so the ``:`` and ``/`` of a URI stay inside the
    directory. A quoted key longer than ``_PART_NAME_MAX`` (a long URI template)
    is cut short and ends in the sha256 of the whole key, which keeps two long
    keys with a common prefix apart. MCP tool names are far shorter, so a tool's
    part name is its plain quoted name.
    """
    name = _url_quote(key, safe="")
    if len(name) > _PART_NAME_MAX:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        name = name[: _PART_NAME_MAX - len(digest) - 1] + "-" + digest
    return name + ".json"


def _merge_manifest_path(ns: argparse.Namespace, target: Path) -> Path | None:
    """Locate the tool-inventory manifest for `merge`'s stale-entry advisory.

    --manifest wins; otherwise <stem>.mcpgen.json beside the shapes file;
    otherwise the sole *.mcpgen.json in that directory, when there is exactly
    one and its `server` field matches — a foreign manifest would drive
    confident, wrong "retired tool" warnings.  Returns None when nothing
    resolves; the advisory then degrades to a count-only line rather than
    failing.
    """
    override = getattr(ns, "manifest", None)
    if override:
        return Path(override)
    stem = target.name[: -len(".shapes.json")] if target.name.endswith(".shapes.json") else target.stem
    beside = target.with_name(stem + ".mcpgen.json")
    if beside.is_file():
        return beside
    candidates = sorted(target.parent.glob("*.mcpgen.json"))
    if len(candidates) != 1:
        return None
    try:
        doc = json.loads(candidates[0].read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return candidates[0] if doc.get("server") == ns.server else None


# shapes.json `kind` → the manifest section that lists entries of that kind.
_MANIFEST_SECTION_FOR_KIND = {kind: section for section, kind in manifest_mod._RESOURCE_SECTIONS}


def _stale_entries(carried: list[str], merged: dict, manifest_doc: Any) -> tuple[list[str], list[str]] | None:
    """Split *carried* shapes keys into `(stale, unchecked)` against *manifest_doc*.

    Returns None when the manifest has no `tools` dict.  A key naming a
    manifest tool is a tool entry whatever it looks like: MCP tool names may
    contain `:`.  Otherwise an entry whose `kind` is `resource` or
    `resource_template` is looked up in that kind's section, and an untagged
    key containing `:` in both sections, since codegen applies an untagged
    entry to either kind.  Every other key is a tool entry and is stale when
    absent from `tools`.

    A resource entry is stale only when every section it could live in is
    present in the manifest and none lists it.  When one of those sections is
    missing, resources were not collected, so the entry is unchecked rather
    than stale.

    Known gap: a retired tool whose name contains `:` reads as an untagged
    resource entry, so while the resource sections are untracked it is
    reported as unchecked, not stale.  Probe always sets `kind`, so untagged
    `:` keys come only from hand edits or tools with such names.
    """
    if not isinstance(manifest_doc, dict) or not isinstance(manifest_doc.get("tools"), dict):
        return None
    tools = manifest_doc["tools"]
    stale: list[str] = []
    unchecked: list[str] = []
    for key in carried:
        if key in tools:
            continue
        entry = merged.get(key)
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if kind in _MANIFEST_SECTION_FOR_KIND:
            names = [_MANIFEST_SECTION_FOR_KIND[kind]]
        elif ":" in key:
            names = list(_MANIFEST_SECTION_FOR_KIND.values())
        else:
            stale.append(key)
            continue
        sections = [manifest_doc.get(name) for name in names]
        if any(isinstance(s, dict) and key in s for s in sections):
            continue
        if all(isinstance(s, dict) for s in sections):
            stale.append(key)
        else:
            unchecked.append(key)
    return stale, unchecked


def _normalize_shapes(shapes: dict) -> list[str]:
    """Normalize type-annotation strings in *shapes* in-place.

    Rewrites JSON/TS-cased tokens (``any``→``Any``, ``null``→``None``, etc.)
    found in ``fields``, ``input_overrides``, and ``return_model`` values.
    Returns a list of change descriptions suitable for a stderr report.
    """
    changes: list[str] = []
    for spec in shapes.values():
        if not isinstance(spec, dict):
            continue
        for fname, ftype in list((spec.get("fields") or {}).items()):
            if isinstance(ftype, str):
                new = codegen.normalize_annotation(ftype)
                if new != ftype:
                    spec["fields"][fname] = new
                    changes.append(f"'{ftype}'→'{new}'")
        for pname, ptype in list((spec.get("input_overrides") or {}).items()):
            if isinstance(ptype, str):
                new = codegen.normalize_annotation(ptype)
                if new != ptype:
                    spec["input_overrides"][pname] = new
                    changes.append(f"'{ptype}'→'{new}'")
        rm = spec.get("return_model")
        if isinstance(rm, str):
            new = codegen.normalize_annotation(rm)
            if new != rm:
                spec["return_model"] = new
                changes.append(f"'{rm}'→'{new}'")
    return changes


def _load_shapes(ns: argparse.Namespace) -> dict | None:
    """Load the shape-spec sidecar: explicit --shapes, else <server>.shapes.json beside --out.

    Fallback: if the shapes file is absent but its .parts/ directory exists (i.e.
    probes ran but `mcpgen merge` was not yet called), merge the parts in-memory
    so `codegen` still works without requiring an explicit merge step.
    """
    path = None
    if ns.shapes:
        path = Path(ns.shapes)
    elif ns.out:
        sibling = Path(ns.out).with_name(f"{_server_stem(ns.server)}.shapes.json")
        if sibling.is_file():
            path = sibling
        else:
            # In-memory fallback: merge any parts that exist.
            parts_d = _parts_dir(sibling)
            if parts_d.is_dir():
                parts = sorted(parts_d.glob("*.json"))
                if parts:
                    skeletons = [json.loads(p.read_text()) for p in parts]
                    shapes = codegen.merge_skeletons(skeletons)
                    changes = _normalize_shapes(shapes)
                    print(
                        f"[codegen] shapes: {parts_d}/ ({len(shapes)} tool(s), "
                        "in-memory merge — run `mcpgen merge` to persist)",
                        file=sys.stderr,
                    )
                    if changes:
                        print(
                            f"[codegen] shapes: normalized {len(changes)} type string(s): " + ", ".join(changes),
                            file=sys.stderr,
                        )
                    return shapes
    if path is None:
        return None
    shapes = json.loads(path.read_text())
    changes = _normalize_shapes(shapes)
    print(f"[codegen] shapes: {path} ({len(shapes)} tool(s))", file=sys.stderr)
    if changes:
        print(
            f"[codegen] shapes: normalized {len(changes)} type string(s): " + ", ".join(changes),
            file=sys.stderr,
        )
    return shapes


def _manifest_path(ns: argparse.Namespace) -> Path | None:
    """Resolve where codegen should write the tool-inventory manifest.

    Derived from --out (``gen/wrapper.py`` → ``gen/wrapper.mcpgen.json``) so the
    manifest stays beside the module it describes even when the module name
    differs from the server name. --manifest overrides; --no-manifest and
    stdout mode suppress.
    """
    if getattr(ns, "no_manifest", False):
        return None
    override = getattr(ns, "manifest", None)
    if override:
        return Path(override)
    if not ns.out:
        return None
    out = Path(ns.out)
    return out.with_name(out.stem + ".mcpgen.json")


def _cmd_codegen(ns: argparse.Namespace) -> int:
    cmd = getattr(ns, "stdio", None)
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        creds_path=_creds_path(ns),
        env=_parse_env(ns),
    )
    try:
        tools = asyncio.run(_list_tools(ns.server, cmd=cmd, **conn))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[codegen] error: {exc}", file=sys.stderr)
        return 1
    print(f"[codegen] {ns.server}: {len(tools)} tools", file=sys.stderr)

    shapes = _load_shapes(ns)

    probe_note = ""
    if ns.probe:
        args = json.loads(ns.probe_args) if ns.probe_args else {}
        print(f"[codegen] probing {ns.probe}({args}) …", file=sys.stderr)
        try:
            shape, _size, _raw = asyncio.run(_probe(ns.server, ns.probe, args, cmd=cmd, **conn))
        except (FileNotFoundError, ValueError) as exc:
            print(f"[codegen] error: {exc}", file=sys.stderr)
            return 1
        shape_json = json.dumps(shape, indent=2)
        probe_note = f"\nObserved response shape of {ns.probe!r} (keys/types/nesting only):\n" + shape_json

    source = codegen.render_module(
        ns.server, tools, shapes=shapes, probe_note=probe_note, embed_schema=getattr(ns, "embed_schema", False)
    )
    if ns.out:
        Path(ns.out).write_text(source)
        print(f"[codegen] wrote {ns.out} ({len(source)} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(source)

    target = _manifest_path(ns)
    if target is not None:
        _atomic_write_text(target, manifest_mod.dumps(manifest_mod.build(ns.server, tools)))
        print(f"[codegen] wrote {target} ({len(tools)} tool(s))", file=sys.stderr)
    return 0


def _resource_probe_plan(target: str, raw_args: list[str]) -> tuple[str, str, list[dict], list[str]]:
    """Resolve a `probe --resource` target into `(key, kind, args_list, uris)`.

    A template (a target containing ``{``) is keyed by the template string as
    given, and each --args value is one probe's variables, recorded as its
    probed_args. A static URI is keyed by its normalized form and takes no
    --args, so it is probed once with ``{}``. Every URI is resolved before the
    first live read. Raises ``ValueError``.
    """
    if "{" in target:
        args_list = [_json_object_arg(a) for a in raw_args] or [{}]
        return target, "resource_template", args_list, [_resource_uri(target, v) for v in args_list]
    if raw_args:
        raise _static_args_error(target)
    uri = _resource_uri(target, None)
    return uri, "resource", [{}], [uri]


def _refuse_part_collision(part: Path, key: str, server: str) -> bool:
    """Print the refusal and return True when *part* holds another key (see ``_part_collision``)."""
    other = _part_collision(part, key)
    if other is None:
        return False
    print(
        f"[probe] error: refusing to overwrite {part}: it holds {other!r}, whose part file name "
        f"matches {key!r} on this case-insensitive filesystem. Run `mcpgen merge {server}` to "
        f"consolidate it first, or delete the part if {other!r} is gone.",
        file=sys.stderr,
    )
    return True


def _part_collision(part: Path, key: str) -> str | None:
    """The key already stored in *part* when it is not *key*, else ``None``.

    Part names are case-preserving, but a case-insensitive filesystem (macOS,
    Windows defaults) maps ``demo://x/Users`` and ``demo://x/users`` to one
    file, and writing the second would silently drop the first. A missing or
    unreadable part holds nothing worth keeping.
    """
    try:
        doc = json.loads(part.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not doc or key in doc:
        return None
    return next(iter(doc))


def _cmd_probe(ns: argparse.Namespace) -> int:
    """Run one or more live calls and emit a merged shape-spec SKELETON.

    Pass --args once for a single probe (original behaviour, byte-stable).
    Pass --args multiple times for multi-probe mode: each call is made in
    sequence and the observed shapes are deep-merged so the skeleton reflects
    the union of all responses (nullable fields, key-presence variance, etc.).

    With --resource the target is a resource URI or URI template, read through
    ``read_resource`` instead of called; see ``_resource_probe_plan`` for the
    entry key and what --args means there. Resource entries carry ``kind`` and
    get no discriminator advisory.

    The deterministic part: top-level scalars → fields; unwrap, types, and
    deep-nest decisions are the skill's judgment (see SKILL.md step 4).
    """
    cmd = getattr(ns, "stdio", None)
    raw_args_list: list[str] = ns.args or []
    kind: str | None = None
    if getattr(ns, "resource", False):
        try:
            key, kind, args_list, uris = _resource_probe_plan(ns.tool, raw_args_list)
        except ValueError as exc:
            print(f"[probe] error: {exc}", file=sys.stderr)
            return 1
    else:
        key = ns.tool
        args_list = [json.loads(a) for a in raw_args_list] if raw_args_list else [{}]
    n = len(args_list)

    save_raw = getattr(ns, "save_raw", None)
    if save_raw:
        # .gitignore ignores *.probe-raw.json; requiring the suffix is what keeps
        # a PII-bearing payload out of git.  Advice in --help is not enforcement.
        if not Path(save_raw).name.endswith(".probe-raw.json"):
            print(
                f"[probe] error: refusing to write raw payload to {Path(save_raw).name} — "
                "the name must end in .probe-raw.json (the git-ignored pattern)",
                file=sys.stderr,
            )
            return 1

    part: Path | None = None
    if ns.emit_shape:
        part = _parts_dir(Path(ns.emit_shape)) / _part_filename(key)
        # Checked before any live call, so a refused write costs no probe, and
        # again before the write for a concurrent probe of a case-twin key.
        if _refuse_part_collision(part, key, ns.server):
            return 1

    label = f"{ns.server}.{ns.tool}" if kind is None else f"{ns.server} {key}"
    print(f"[probe] {label} ({n} probe(s)) …", file=sys.stderr)

    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        creds_path=_creds_path(ns),
        env=_parse_env(ns),
    )
    shapes = []
    sizes = []
    raws: list[Any] = []
    for i, args in enumerate(args_list):
        if kind is None:
            print(f"[probe]   [{i + 1}/{n}] args={args}", file=sys.stderr)
        else:
            print(f"[probe]   [{i + 1}/{n}] {uris[i]} args={args}", file=sys.stderr)
        # one session per probe (prototype); pooling is out of scope
        try:
            if kind is None:
                shape, size, raw = asyncio.run(_probe(ns.server, ns.tool, args, cmd=cmd, **conn))
            else:
                shape, size, raw = asyncio.run(_probe_resource(ns.server, uris[i], cmd=cmd, **conn))
        except (FileNotFoundError, ValueError) as exc:
            print(f"[probe] error: {exc}", file=sys.stderr)
            return 1
        except McpError as exc:
            print(f"[probe] error: {_bridge._describe(exc)}", file=sys.stderr)
            return 1
        except BaseExceptionGroup as exc:
            _check_reportable(exc)
            print(f"[probe] error: {_bridge._describe(exc)}", file=sys.stderr)
            return 1
        shapes.append(shape)
        sizes.append(size)
        raws.append(raw)

    skeleton = codegen.probe_skeleton(key, args_list, shapes, observed_bytes=sizes, kind=kind)
    out = json.dumps(skeleton, indent=2)
    if part is not None:
        if _refuse_part_collision(part, key, ns.server):
            return 1
        _atomic_write_text(part, out + "\n")
        print(f"[probe] wrote part {part}", file=sys.stderr)
        print(f"[probe] run `mcpgen merge {ns.server}` to consolidate into {ns.emit_shape}", file=sys.stderr)
    else:
        sys.stdout.write(out + "\n")

    if save_raw:
        if len(raws) == 1 and isinstance(raws[0], str):
            text = raws[0]
        elif len(raws) == 1:
            text = json.dumps(raws[0], indent=2, default=_raw_default)
        else:
            text = json.dumps(
                [{"args": a, "raw": r} for a, r in zip(args_list, raws, strict=True)], indent=2, default=_raw_default
            )
        raw_target = Path(save_raw)
        _atomic_write_text(raw_target, text + "\n")
        kb = len(text.encode()) / 1024
        print(f"[probe] wrote raw payload ({kb:.1f} KB) to {raw_target}", file=sys.stderr)
        print(
            "[probe] ⚠  raw payload contains real ids/PII — git-ignored here, but do not paste it "
            "into a committed file.",
            file=sys.stderr,
        )

    if kind is not None:
        # A resource's variables pick which resource is read, not a response variant.
        return 0

    try:
        _DISCRIMINATOR_KEYS = {"entitytype", "type", "kind", "category", "entity_type", "objecttype", "resourcetype"}
        all_keys: set[str] = set().union(*(a.keys() for a in args_list))
        for key in sorted(all_keys):
            # Skip non-hashable values (lists, dicts) — discriminators are scalars by definition.
            values = {
                args[key]
                for args in args_list
                if key in args and isinstance(args[key], (str, int, float, bool, type(None)))
            }
            if len(values) == 1 and key.lower() in _DISCRIMINATOR_KEYS:
                val = next(iter(values))
                print(f"[probe] ⚠  {key} probed as {val!r} only — response shape is variant-specific.", file=sys.stderr)
                print(
                    "[probe]    Do NOT emit a single-variant model. Probe every value or use a base model (SKILL step 4).",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(f"[probe] ⚠  discriminator advisory skipped ({exc})", file=sys.stderr)

    return 0


def _cmd_call(ns: argparse.Namespace) -> int:
    """Make one live tool call and write the raw parsed payload to --out.

    Unlike `probe`, this preserves the full response — values, ids, etc.
    Use it for bootstrapping (e.g. call a no-arg whoami first to get real ids)
    or inspecting a tool's actual output.  The --out file is required to avoid
    flooding model context with large payloads; use a *.probe-raw.json name
    so it stays git-ignored.
    """
    cmd = getattr(ns, "stdio", None)
    try:
        args: dict = json.loads(ns.args) if ns.args else {}
    except json.JSONDecodeError as exc:
        print(f"[call] error: --args must be valid JSON ({exc})", file=sys.stderr)
        return 1
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        creds_path=_creds_path(ns),
        env=_parse_env(ns),
    )

    print(f"[call] {ns.server}.{ns.tool} (live) …", file=sys.stderr)
    try:
        raw = asyncio.run(_call(ns.server, ns.tool, args, cmd=cmd, **conn))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[call] error: {exc}", file=sys.stderr)
        return 1

    text = raw if isinstance(raw, str) else json.dumps(raw, indent=2)
    out = Path(ns.out)
    _atomic_write_text(out, text + "\n")

    kb = len(text.encode()) / 1024
    print(f"[call] wrote raw payload ({kb:.1f} KB) to {out}", file=sys.stderr)
    print(
        "[call] ⚠  raw payload may contain real ids/PII — use a *.probe-raw.json name (git-ignored).",
        file=sys.stderr,
    )
    return 0


def _read_output(result: ReadResourceResult) -> bytes:
    """The bytes `read --out` writes for one ``resources/read`` result.

    A single text content is its text, verbatim; a single blob is its decoded
    bytes. Several contents are a JSON array of the contents as the server sent
    them, so each keeps its own uri and mimeType. Raises ``ValueError`` for no
    contents or invalid base64.
    """
    contents = result.contents
    if not contents:
        raise ValueError("MCP resource read returned no contents")
    if len(contents) > 1:
        dumps = [c.model_dump(mode="json", by_alias=True, exclude_none=True) for c in contents]
        return (json.dumps(dumps, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    item = contents[0]
    if isinstance(item, TextResourceContents):
        return item.text.encode("utf-8")
    return _bridge.parse_resource([item])


def _cmd_read(ns: argparse.Namespace) -> int:
    """Read one resource live and write its contents to --out.

    The resource-side twin of `call`: the payload goes to a file, never to
    stdout. A URI template target is expanded from --args first.
    """
    cmd = getattr(ns, "stdio", None)
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        creds_path=_creds_path(ns),
        env=_parse_env(ns),
    )
    try:
        values = _json_object_arg(ns.args) if ns.args is not None else None
        uri = _resource_uri(ns.uri, values)
        print(f"[read] {ns.server} {uri} (live) …", file=sys.stderr)
        data = _read_output(asyncio.run(_read_resource(ns.server, uri, cmd=cmd, **conn)))
        out = Path(ns.out)
        _atomic_write_bytes(out, data)
    except (OSError, ValueError) as exc:
        print(f"[read] error: {exc}", file=sys.stderr)
        return 1
    except McpError as exc:
        print(f"[read] error: {_bridge._describe(exc)}", file=sys.stderr)
        return 1
    except BaseExceptionGroup as exc:
        _check_reportable(exc)
        print(f"[read] error: {_bridge._describe(exc)}", file=sys.stderr)
        return 1
    print(f"[read] wrote {len(data) / 1024:.1f} KB to {out}", file=sys.stderr)
    print(
        "[read] ⚠  resource contents may contain real ids/PII — use a *.probe-raw.json name (git-ignored).",
        file=sys.stderr,
    )
    return 0


def _cmd_merge(ns: argparse.Namespace) -> int:
    """Consolidate per-tool part files into a single <server>.shapes.json.

    Probing in parallel writes one part file per tool under
    <target>.parts/<tool>.json.  This command merges all parts into the
    committed, hand-editable shapes sidecar, preserving any tool entries that
    were NOT re-probed (i.e. already present in an existing shapes.json and
    absent from the parts dir).
    """
    target = Path(ns.out) if ns.out else Path(f"{_server_stem(ns.server)}.shapes.json")
    parts_d = _parts_dir(target)

    if not parts_d.is_dir():
        print(
            f"[merge] no parts dir {parts_d} — nothing to merge"
            + (" (probed into a subfolder? pass --out <dir>/<server>.shapes.json)" if not ns.out else ""),
            file=sys.stderr,
        )
        return 0

    parts = sorted(parts_d.glob("*.json"))
    if not parts:
        print(f"[merge] parts dir {parts_d} is empty — nothing to merge", file=sys.stderr)
        return 0

    # Load base (existing shapes.json) so hand-edited entries for un-probed
    # tools are preserved.  Parts for re-probed tools override the base.
    base: dict = {}
    if target.is_file():
        base = json.loads(target.read_text())
        print(f"[merge] base: {target} ({len(base)} entry(ies))", file=sys.stderr)

    part_skeletons = [json.loads(p.read_text()) for p in parts]
    merged = codegen.merge_skeletons([base] + part_skeletons)

    # Verify sidecar FIRST, from the raw parts — it is the gold source for the
    # roundtrip verifier and is gitignored, so it keeps pre-scrub values.
    # Omit no-arg tools (probed_args == {}).  Overlay an existing sidecar so
    # partial re-probes preserve prior entries.
    stem = target.name[: -len(".shapes.json")] if target.name.endswith(".shapes.json") else target.stem
    verify_target = target.with_name(stem + ".verify.json")
    verify_map: dict = {}
    if verify_target.is_file():
        try:
            verify_map = json.loads(verify_target.read_text())
        except (OSError, json.JSONDecodeError):
            verify_map = {}
    for sk in part_skeletons:
        for tool_name, entry in sk.items():
            if isinstance(entry, dict):
                pa = entry.get("probed_args")
                if pa:  # non-empty dict or non-empty list
                    verify_map[tool_name] = pa
    # Prune entries for tools no longer part of the merged shapes (e.g. the
    # server dropped the tool) — otherwise dead entries persist forever.
    verify_map = {k: v for k, v in verify_map.items() if k in merged}

    # Scrub only what gets committed.  shapes.json is version-controlled; the
    # parts dir and the verify sidecar are not.
    if getattr(ns, "no_scrub", False):
        print(
            "[merge] ⚠  --no-scrub: probed_args written verbatim to a committed file — "
            "check it for real ids/PII before committing.",
            file=sys.stderr,
        )
    else:
        merged, scrubbed_tools = codegen.scrub_skeletons(merged)
        if scrubbed_tools:
            print(
                f"[merge] scrubbed probed_args for {len(scrubbed_tools)} entry(ies):{', '.join(scrubbed_tools)}",
                file=sys.stderr,
            )
            print(
                f"[merge]    raw values kept in {verify_target.name} (git-ignored).",
                file=sys.stderr,
            )

    _atomic_write_text(target, json.dumps(merged, indent=2) + "\n")
    print(f"[merge] wrote {target} ({len(merged)} entry(ies))", file=sys.stderr)

    if verify_map:
        _atomic_write_text(verify_target, json.dumps(verify_map, indent=2) + "\n")
        print(
            f"[merge] wrote {verify_target} ({len(verify_map)} entry(ies))"
            " — ⚠  raw args/PII, git-ignored (verify sidecar)",
            file=sys.stderr,
        )
    elif verify_target.is_file():
        # Every entry was pruned as stale — don't leave dead content on disk.
        verify_target.unlink()
        print(f"[merge] removed {verify_target} (all entries stale)", file=sys.stderr)

    # Stale-entry advisory.  Merge is offline by contract — it makes no list
    # calls — so it cannot know the server's current inventory.  It
    # cross-checks entries that were carried forward unprobed against the last
    # recorded manifest instead, and only warns: dropping on this evidence
    # would silently delete hand-edited shape specs.
    probed_now = {name for sk in part_skeletons for name in sk}
    carried = sorted(set(merged) - probed_now)
    if carried:
        manifest_p = _merge_manifest_path(ns, target)
        manifest_doc: Any = None
        manifest_name = ""
        if manifest_p is not None and manifest_p.is_file():
            manifest_name = manifest_p.name
            try:
                manifest_doc = json.loads(manifest_p.read_text())
            except (OSError, json.JSONDecodeError):
                manifest_doc = None
        checked = _stale_entries(carried, merged, manifest_doc)
        if checked is not None:
            stale, unchecked = checked
            if stale:
                print(
                    f"[merge] ⚠  {len(stale)} shapes entry(ies) absent from {manifest_name}: {', '.join(stale)}",
                    file=sys.stderr,
                )
                print(
                    "[merge]    They were carried forward unprobed and may be retired. "
                    "Remove them by hand if the server no longer exposes them.",
                    file=sys.stderr,
                )
            if unchecked:
                # Count only, like the no-manifest line below: these are
                # undetectable, not suspect.
                print(
                    f"[merge] {len(unchecked)} resource entry(ies) not checked: "
                    f"{manifest_name} does not track resources. Run `mcpgen codegen` to record them.",
                    file=sys.stderr,
                )
        else:
            # No manifest: retired entries are undetectable here.  Carrying
            # entries forward unprobed is the normal partial-re-probe case
            # (re-probing one tool on a 40-tool server carries 39), so report the
            # count only — enumerating names would make every partial merge noisy.
            print(
                f"[merge] {len(carried)} entry(ies) carried forward unprobed; no inventory manifest "
                "beside the shapes file, so retired entries cannot be detected. "
                "Run `mcpgen codegen` to write one.",
                file=sys.stderr,
            )

    if not ns.keep_parts:
        shutil.rmtree(parts_d)
        print(f"[merge] removed {parts_d}", file=sys.stderr)

    return 0


def _cmd_discover(ns: argparse.Namespace) -> int:
    """List MCP servers from installed agent host environments."""
    host_filter: list[str] | None = ns.host  # None or list of ids
    try:
        servers = discovery.discover_all(hosts=host_filter)
    except Exception as exc:
        print(f"[discover] error: {exc}", file=sys.stderr)
        return 1

    if ns.json:
        sys.stdout.write(json.dumps([s.as_dict(redact_env=not ns.include_env) for s in servers], indent=2) + "\n")
        return 0

    # ------------------------------------------------------------------ #
    # Human table — grouped by host                                        #
    # ------------------------------------------------------------------ #

    # Build provider display_name lookup: id -> display_name
    provider_names: dict[str, str] = {p.id: p.display_name for p in discovery.PROVIDERS}

    # Group servers by host id, preserving insertion order
    groups: dict[str, list[discovery.DiscoveredServer]] = {}
    for s in servers:
        groups.setdefault(s.host, []).append(s)

    # Determine which host ids to show (respecting filter)
    if host_filter is not None:
        host_ids = [h for h in host_filter if h in groups]
    else:
        host_ids = list(groups.keys())

    col_name = 25
    col_transport = 10
    col_scope = 18

    for hid in host_ids:
        display = provider_names.get(hid, hid)
        print(f"=== {display} ===")
        print()

        group = groups.get(hid, [])
        if not group:
            print("  (no servers found)")
            print()
            continue

        for s in group:
            # Truncate scope label at first " ("
            scope_label = s.scope or ""
            if " (" in scope_label:
                scope_label = scope_label.split(" (", 1)[0]

            status_label = s.status or ""
            name_col = s.name.ljust(col_name)
            transport_col = s.transport.ljust(col_transport)
            scope_col = scope_label.ljust(col_scope)
            print(f"  {name_col}{transport_col}{scope_col}{status_label}")

            if s.probeable:
                if s.transport == "stdio" and s.command:
                    cmd_str = s.command + (" " + " ".join(s.args) if s.args else "")
                    print(f'    → mcpgen list {s.name} --stdio "{cmd_str}"')
                elif s.transport in ("http", "sse") and s.url:
                    print(f"    → mcpgen list {s.name} --url {s.url}")
                else:
                    print(f"    (hint unavailable — transport: {s.transport})")
            else:
                if s.note:
                    print(f"  ⚠  {s.note}")

        print()

    return 0


# Fields `list --resources` prints per entry, in this order. Everything else the
# server sends is dropped: `icons[].src` may be a whole data: URI, and `_meta` /
# `annotations` / vendor extras are not part of the inventory mcpgen acts on.
_LISTED_RESOURCE_FIELDS = ("uri", "name", "title", "description", "mimeType", "size")
_LISTED_TEMPLATE_FIELDS = ("uriTemplate", "name", "title", "description", "mimeType")


def _pick(entry: dict, fields: tuple[str, ...]) -> dict:
    return {key: entry[key] for key in fields if key in entry}


def _cmd_list(ns: argparse.Namespace) -> int:
    """Print the tool inventory as JSON [{name, description}] for a server.

    With --resources, print {"resources": [...], "resourceTemplates": [...]}
    instead. Without it, the first page of each resource list is read in the
    same session, non-strict, only to print a stderr hint when the server has
    any; a resource failure there becomes a stderr note, never a failed `list`.
    """
    cmd = getattr(ns, "stdio", None)
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        creds_path=_creds_path(ns),
        env=_parse_env(ns),
    )
    want_resources = getattr(ns, "resources", False)
    try:
        inv = asyncio.run(_list_inventory(ns.server, scope="all", strict=want_resources, cmd=cmd, **conn))
    except ResourceListingFailed as exc:
        print(f"[list] error: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, McpError) as exc:
        print(f"[list] error: {_bridge._describe(exc)}", file=sys.stderr)
        return 1
    except BaseExceptionGroup as exc:
        _check_reportable(exc)
        print(f"[list] error: {_bridge._describe(exc)}", file=sys.stderr)
        return 1

    if want_resources:
        payload = {
            "resources": [_pick(r, _LISTED_RESOURCE_FIELDS) for r in inv.resources or []],
            "resourceTemplates": [_pick(t, _LISTED_TEMPLATE_FIELDS) for t in inv.templates or []],
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    tools = inv.tools

    def _entry(t: dict) -> dict:
        entry = {"name": t["name"], "description": t.get("description") or ""}
        if getattr(ns, "schema", False):
            entry["inputSchema"] = t.get("inputSchema") or {}
        annotations = t.get("annotations")
        if annotations is not None:
            entry["annotations"] = annotations
        return entry

    out = [_entry(t) for t in tools]
    sys.stdout.write(json.dumps(out, indent=2) + "\n")

    candidates = codegen.detect_discriminators(tools)
    if candidates:
        print("[list] ⚠  discriminator candidates (response shape varies by these args):", file=sys.stderr)
        for param, tool_names in candidates.items():
            print(f"[list]   {param} → {', '.join(tool_names)}", file=sys.stderr)
        print(
            "[list]   Probe EACH value or use a base model — do NOT type from one probe. See SKILL step 4.",
            file=sys.stderr,
        )

    if inv.resource_error is not None:
        print(f"[list] note: could not list resources: {_bridge._describe(inv.resource_error)}", file=sys.stderr)
    elif inv.resources or inv.templates or inv.resources_truncated or inv.templates_truncated:
        # Counts come from the first page only; "N+" marks a list with more pages.
        n_resources = f"{len(inv.resources or [])}{'+' if inv.resources_truncated else ''}"
        n_templates = f"{len(inv.templates or [])}{'+' if inv.templates_truncated else ''}"
        print(
            f"[list] {n_resources} resource(s) and {n_templates} resource template(s) available; "
            "re-run with `--resources` to list them",
            file=sys.stderr,
        )

    return 0


# Exit contract for `mcpgen check`. Deliberately different from the other
# subcommands, which return 1 for operational failures: here 1 means drift and
# only drift, so a CI job with a dead token or an unreachable server can never
# be misread as a changed tool contract.
CHECK_OK = 0
CHECK_DRIFT = 1
CHECK_ERROR = 2


def _cmd_check(ns: argparse.Namespace) -> int:
    """Compare a server's live tool inventory against a stored manifest."""
    manifest_path = Path(ns.manifest) if ns.manifest else Path(f"{ns.server}.mcpgen.json")

    def _fail(message: str) -> int:
        if getattr(ns, "json", False):
            sys.stdout.write(json.dumps({"server": ns.server, "error": message}, indent=2) + "\n")
        print(f"[check] error: {message}", file=sys.stderr)
        return CHECK_ERROR

    bootstrapping = False
    try:
        stored = json.loads(manifest_path.read_text())
    except FileNotFoundError:
        if not getattr(ns, "update", False):
            return _fail(
                f"manifest not found: {manifest_path}. Generate one with `mcpgen codegen --out <file>.py`, "
                f"or bootstrap it with `mcpgen check {ns.server} --manifest {manifest_path} --update`."
            )
        bootstrapping = True
        stored = manifest_mod.build(ns.server, [])
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(f"cannot read manifest {manifest_path}: {exc}")

    cmd = getattr(ns, "stdio", None)
    conn = dict(
        url=ns.url,
        bearer=ns.bearer,
        client_name=ns.client_name,
        config_path=ns.config,
        cred_backend=ns.cred_backend,
        creds_path=_creds_path(ns),
        env=_parse_env(ns),
    )
    # No probing: tools/list only. `check` never calls a tool and never touches
    # the shape-spec sidecar.
    try:
        tools = asyncio.run(_list_tools(ns.server, cmd=cmd, **conn))
    except Exception as exc:  # transport, auth, config — all operational
        return _fail(f"{type(exc).__name__}: {exc}")

    try:
        live = manifest_mod.build(ns.server, tools)
        report = manifest_mod.diff(stored, live)
    except ValueError as exc:
        return _fail(str(exc))

    updated = False
    if getattr(ns, "update", False):
        # The only write path outside `codegen`, and it requires the explicit
        # flag. Nothing above this line ever touches the manifest file.
        _atomic_write_text(manifest_path, manifest_mod.dumps(live))
        updated = True

    if getattr(ns, "json", False):
        payload = manifest_mod.to_json(report)
        if updated:
            payload["updated"] = True
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        sys.stdout.write(manifest_mod.render_text(report) + "\n")
        if updated:
            accepted = len(report.added) + len(report.removed) + len(report.changed)
            verb = "created" if bootstrapping else "updated"
            sys.stdout.write(f"[check] {verb} {manifest_path} (accepted {accepted} change(s))\n")

    if updated:
        return CHECK_OK
    return CHECK_DRIFT if report.has_drift else CHECK_OK


def _cmd_login(ns: argparse.Namespace) -> int:
    try:
        asyncio.run(
            _bridge.login(
                ns.server,
                _creds_path(ns) or _bridge.DEFAULT_CREDS_PATH,
                url=ns.url,
                client_name=ns.client_name,
                config_path=ns.config,
                cred_backend=ns.cred_backend,
                headless=ns.headless,
                callback_timeout=ns.callback_timeout,
            )
        )
    except _bridge.LoginWontHelp as exc:
        # The server is at fault, not the login. A traceback here reads as "your login
        # broke" and invites a pointless second browser round. The base is caught because
        # every member of this taxonomy means the same thing to a CLI user: the message,
        # exit 1, no traceback. A subclass needing other handling goes above this clause.
        print(f"[login] error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_migrate_creds(ns: argparse.Namespace) -> int:
    parsed_servers: list[str] | None = None
    if ns.servers:
        parsed_servers = [s.strip() for s in ns.servers.split(",") if s.strip()]
        if not parsed_servers:
            parsed_servers = None

    result = _bridge.migrate_creds(
        ns.from_backend,
        ns.to_backend,
        servers=parsed_servers,
        credentials_path=_creds_path(ns) or _bridge.DEFAULT_CREDS_PATH,
        purge=ns.purge,
        set_default=ns.set_default,
    )

    n = result["migrated"]
    ow = result["overwritten"]
    src = result["from"]
    dst = result["to"]
    purged_note = "source purged" if result["purged"] else "source kept"
    default_note = f"; default set to {dst!r}" if result["set_default"] else ""
    print(
        f"[migrate-creds] copied {n} server(s) {src} → {dst} ({ow} overwritten); {purged_note}{default_note}",
        file=sys.stderr,
    )
    return 0


def _cmd_list_creds(ns: argparse.Namespace) -> int:
    """Print stored credentials as a human table (default) or JSON (--json)."""
    rows = _bridge.list_creds(
        backend=ns.cred_backend,
        credentials_path=_creds_path(ns) or _bridge.DEFAULT_CREDS_PATH,
        expired_only=ns.expired,
    )

    if ns.json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return 0

    if not rows:
        msg = "no expired credentials stored" if ns.expired else "no credentials stored"
        print(f"[list-creds] {msg}", file=sys.stderr)
        return 0

    # Human table: NAME / STATUS / EXPIRES
    col_name = max(len(r["name"]) for r in rows)
    col_name = max(col_name, 4)  # min header width "NAME"
    col_status = 9  # longest status value: "no-expiry"
    header = "NAME".ljust(col_name) + "  " + "STATUS".ljust(col_status) + "  " + "EXPIRES"
    print(header)
    print("-" * len(header))
    for r in rows:
        if r["expired"]:
            status = "expired"
        elif r["expires_at"] is None:
            status = "no-expiry"
        else:
            status = "valid"
        if r["expires_at"] is not None:
            expires = datetime.fromtimestamp(r["expires_at"]).isoformat(timespec="seconds")
        else:
            expires = "-"
        print(r["name"].ljust(col_name) + "  " + status.ljust(col_status) + "  " + expires)
    return 0


def _cmd_delete_creds(ns: argparse.Namespace) -> int:
    """Delete a single stored credential by server name."""
    if not ns.yes:
        print(
            f"[delete-creds] delete stored credential for {ns.server!r}? [y/N] ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        answer = input().strip().lower()
        if answer not in ("y", "yes"):
            print("[delete-creds] aborted", file=sys.stderr)
            return 0

    existed = _bridge.delete_cred(
        ns.server,
        backend=ns.cred_backend,
        credentials_path=_creds_path(ns) or _bridge.DEFAULT_CREDS_PATH,
    )
    if existed:
        print(f"[delete-creds] deleted {ns.server!r}", file=sys.stderr)
    else:
        print(f"[delete-creds] no stored credential for {ns.server!r}", file=sys.stderr)
    return 0


def _add_headless_flag(p: argparse.ArgumentParser) -> None:
    """Add --headless / --no-headless to a subparser (absent → None → auto-detect)."""
    p.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print the auth URL and read the pasted callback URL from stdin "
        "instead of opening a browser. --no-headless forces the browser flow. "
        "Default: auto-detect (headless without DISPLAY/WAYLAND_DISPLAY; "
        "override with MCPGEN_HEADLESS).",
    )


def _add_creds_flag(p: argparse.ArgumentParser, *, transport_note: bool = True) -> None:
    """Add --creds PATH — the file backend's credentials store.

    Shared by the connection commands and the credential-management ones, so a
    non-default store stays addressable end to end: login writes it, calls read
    it, list/delete/migrate operate on it.
    """
    note = (
        " No effect with --bearer or --stdio, which store no credentials."
        if transport_note
        else " Only the file backend has a path; the keyring backend ignores it."
    )
    p.add_argument(
        "--creds",
        dest="creds",
        metavar="PATH",
        help=f"OAuth credentials file for both storing and reading tokens "
        f"(default: {_bridge.DEFAULT_CREDS_PATH}).{note}",
    )


def _creds_path(ns: argparse.Namespace) -> Path | None:
    """--creds as a Path, or None to let _bridge use DEFAULT_CREDS_PATH."""
    value = getattr(ns, "creds", None)
    return Path(value) if value else None


def _callback_timeout(value: str) -> float:
    """argparse type for --callback-timeout: a non-negative number of seconds."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of seconds, got {value!r}") from None
    if seconds < 0 or seconds != seconds:  # NaN never equals itself
        raise argparse.ArgumentTypeError(f"must be >= 0 (0 waits forever), got {value!r}")
    return seconds


def _add_conn_args(p: argparse.ArgumentParser) -> None:
    """Inline server-connection args shared by all commands (override config)."""
    p.add_argument("--url", help="server URL inline; enables OAuth without a config entry")
    p.add_argument(
        "--bearer",
        metavar="TOKEN",
        help="static Bearer token for APIs that use PATs (e.g. GitHub); "
        "bypasses OAuth. Read from $GITHUB_PAT or similar — never "
        "pass a literal token on the command line.",
    )
    p.add_argument(
        "--client-name", dest="client_name", help="OAuth client_name override (shown on the server consent screen)"
    )
    p.add_argument(
        "--config", dest="config", help="servers config path; overrides $MCPGEN_SERVERS and the default search"
    )
    _add_creds_flag(p)
    p.add_argument(
        "--cred-backend",
        dest="cred_backend",
        choices=["file", "keyring", "auto"],
        help="credential storage backend: file (default, hardened 0600), "
        "keyring (OS keychain; falls back to file if unavailable), "
        "or auto (keyring if detected, else file). "
        "Also: MCPGEN_CRED_BACKEND env or ~/.mcpgen/config.json 'cred_backend'.",
    )
    p.add_argument(
        "--env",
        dest="env",
        action="append",
        metavar="KEY[=VAL]",
        help="forward an env var to a --stdio server: 'KEY' reads $KEY from "
        "the shell; 'KEY=VAL' sets it inline. Repeat for multiple vars. "
        "No-op when --stdio is not used.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcpgen")
    parser.add_argument("--version", action="version", version=f"mcpgen {_bridge.package_version()}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cg = sub.add_parser("codegen", help="generate typed wrappers for a server")
    cg.add_argument("server", help="server name (e.g. acme) or URL")
    cg.add_argument("--out", help="output .py path (default: stdout)")
    cg.add_argument("--shapes", help="shape-spec JSON sidecar (default: <server>.shapes.json beside --out)")
    cg.add_argument("--probe", help="tool to call live and record response shape (docstring note only)")
    cg.add_argument("--probe-args", help="JSON args for --probe (default: {})")
    cg.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    cg.add_argument(
        "--embed-schema",
        action="store_true",
        dest="embed_schema",
        help="embed raw inputSchema as __schema__ attribute and Args docstring per tool",
    )
    cg.add_argument(
        "--manifest",
        metavar="PATH",
        help="tool-inventory manifest path for `mcpgen check` (default: <out-stem>.mcpgen.json beside --out)",
    )
    cg.add_argument(
        "--no-manifest",
        action="store_true",
        dest="no_manifest",
        help="do not write a tool-inventory manifest",
    )
    _add_conn_args(cg)
    cg.set_defaults(func=_cmd_codegen)

    pr = sub.add_parser(
        "probe", help="live-call a tool, or read a resource with --resource, and emit a shape-spec skeleton"
    )
    pr.add_argument("server", help="server name (e.g. acme) or URL")
    pr.add_argument("tool", metavar="target", help="tool to call live; with --resource, a resource URI or URI template")
    pr.add_argument(
        "--resource",
        action="store_true",
        help="read the target as a resource through read_resource, the generated accessors' path. "
        "A target containing '{' is a URI template; the part is keyed by the template, "
        "else by the normalized URI",
    )
    pr.add_argument(
        "--args",
        action="append",
        metavar="JSON",
        help="JSON args for one probe call; repeat for multi-probe (default: {}). "
        "With --resource: one probe's URI template variables (template targets only)",
    )
    pr.add_argument("--emit-shape", help="write skeleton to this path (default: stdout)")
    pr.add_argument(
        "--save-raw",
        dest="save_raw",
        metavar="FILE",
        help="also write the untruncated raw payload here; the name MUST end in "
        ".probe-raw.json (git-ignored). Multi-probe writes a JSON array of {args, raw}; "
        "bytes are base64-encoded",
    )
    pr.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(pr)
    pr.set_defaults(func=_cmd_probe)

    cl = sub.add_parser("call", help="live-call a tool and write the raw payload to --out")
    cl.add_argument("server", help="server name (e.g. acme) or URL")
    cl.add_argument("tool", help="tool to call live")
    cl.add_argument("--args", metavar="JSON", default=None, help="JSON args for the tool call (default: {})")
    cl.add_argument(
        "--out", required=True, metavar="FILE", help="write raw payload here; use a *.probe-raw.json name — git-ignored"
    )
    cl.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(cl)
    cl.set_defaults(func=_cmd_call)

    rd = sub.add_parser("read", help="live-read a resource and write its contents to --out")
    rd.add_argument("server", help="server name (e.g. acme) or URL")
    rd.add_argument("uri", help="resource URI, or a URI template such as 'demo://records/{id}' (expanded from --args)")
    rd.add_argument(
        "--args",
        metavar="JSON",
        default=None,
        help="JSON object of URI template variables, keyed by the template's variable names; a template target only",
    )
    rd.add_argument(
        "--out",
        required=True,
        metavar="FILE",
        help="write the contents here: one text content verbatim, one blob as raw bytes, "
        "several as a JSON array. Use a *.probe-raw.json name for text — git-ignored",
    )
    rd.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(rd)
    rd.set_defaults(func=_cmd_read)

    mg = sub.add_parser("merge", help="consolidate per-tool probe parts into <server>.shapes.json")
    mg.add_argument("server", help="server name (e.g. acme) or URL")
    mg.add_argument(
        "--out",
        help=(
            "target shapes file (default: <server>.shapes.json in CWD). "
            "MUST match the path passed to probe --emit-shape when probing into a subfolder, "
            "e.g. --out github/github.shapes.json"
        ),
    )
    mg.add_argument(
        "--keep-parts", action="store_true", help="keep the .parts/ directory after merging (default: remove)"
    )
    mg.add_argument(
        "--no-scrub",
        action="store_true",
        dest="no_scrub",
        help="write probed_args to shapes.json verbatim (default: scrub emails, UUIDs, "
        "home-dir usernames and long numeric ids — shapes.json is a committed file)",
    )
    mg.add_argument(
        "--manifest",
        metavar="PATH",
        help="tool-inventory manifest to cross-check carried-forward entries against "
        "(default: <shapes-stem>.mcpgen.json beside the shapes file)",
    )
    mg.add_argument(
        "--config",
        dest="config",
        help="accepted for flag-surface consistency with other subcommands; "
        "has no effect on merge (merge is pure filesystem consolidation)",
    )
    mg.set_defaults(func=_cmd_merge)

    ls = sub.add_parser(
        "list",
        help="list tools for a server as JSON [{name, description}]; with --resources, its resources instead",
    )
    ls.add_argument("server", help="server name (e.g. acme) or URL")
    ls.add_argument("--schema", action="store_true", help="include raw inputSchema JSON per tool")
    ls.add_argument(
        "--resources",
        action="store_true",
        help='print resources and resource templates as JSON {"resources": [...], "resourceTemplates": [...]} '
        "instead of tools (--schema is ignored)",
    )
    ls.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    _add_conn_args(ls)
    ls.set_defaults(func=_cmd_list)

    ck = sub.add_parser(
        "check",
        help="compare a server's live tool inventory against a stored manifest",
        description="Exit codes: 0 = no drift, 1 = drift, 2 = operational/config/auth error.",
    )
    ck.add_argument("server", help="server name (e.g. acme) or URL")
    ck.add_argument(
        "--manifest",
        metavar="PATH",
        help="manifest written by `mcpgen codegen` (default: <server>.mcpgen.json)",
    )
    ck.add_argument("--stdio", metavar="CMD", help="use stdio transport: 'python server.py' (no auth)")
    ck.add_argument("--json", action="store_true", help="emit a structured report instead of the human diff")
    ck.add_argument(
        "--update",
        action="store_true",
        help="accept the live inventory and rewrite the manifest (never happens without this flag)",
    )
    _add_conn_args(ck)
    ck.set_defaults(func=_cmd_check)

    lg = sub.add_parser("login", help="browser OAuth login for a named server")
    lg.add_argument("server", help="server name (e.g. acme)")
    _add_conn_args(lg)
    _add_headless_flag(lg)
    lg.add_argument(
        "--callback-timeout",
        dest="callback_timeout",
        type=_callback_timeout,
        metavar="SECONDS",
        help="how long to wait for the browser redirect before giving up "
        f"(default: {_bridge._CALLBACK_TIMEOUT}). 0 waits forever. "
        "Ignored in headless mode, where the pasted-URL prompt is never bounded.",
    )
    lg.set_defaults(func=_cmd_login)

    mc = sub.add_parser(
        "migrate-creds",
        help="copy stored credentials between file and keyring backends",
    )
    mc.add_argument(
        "--from",
        dest="from_backend",
        required=True,
        choices=["file", "keyring"],
        help="source backend",
    )
    mc.add_argument(
        "--to",
        dest="to_backend",
        required=True,
        choices=["file", "keyring"],
        help="target backend",
    )
    mc.add_argument(
        "--servers",
        metavar="A,B,C",
        help="comma-separated server names to migrate (default: all stored servers)",
    )
    mc.add_argument(
        "--purge",
        action="store_true",
        help="remove migrated entries from the source after a verified copy (default: keep)",
    )
    mc.add_argument(
        "--set-default",
        dest="set_default",
        action="store_true",
        help="write cred_backend=<to> into ~/.mcpgen/config.json so future "
        "commands default to the target backend (default: leave config untouched)",
    )
    _add_creds_flag(mc, transport_note=False)
    mc.set_defaults(func=_cmd_migrate_creds)

    lc = sub.add_parser("list-creds", help="list stored credentials (flags expired)")
    lc.add_argument(
        "--expired",
        action="store_true",
        help="show only expired credentials (omit valid and non-expiring entries)",
    )
    lc.add_argument(
        "--json",
        action="store_true",
        help="emit JSON array instead of a human table",
    )
    lc.add_argument(
        "--cred-backend",
        dest="cred_backend",
        choices=["file", "keyring", "auto"],
        help="credential storage backend (default: resolved from env/config, else file)",
    )
    _add_creds_flag(lc, transport_note=False)
    lc.set_defaults(func=_cmd_list_creds)

    dl = sub.add_parser("delete-creds", help="delete the stored credential for one server")
    dl.add_argument("server", help="server name whose stored credential to delete")
    dl.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="skip the confirmation prompt",
    )
    dl.add_argument(
        "--cred-backend",
        dest="cred_backend",
        choices=["file", "keyring", "auto"],
        help="credential storage backend (default: resolved from env/config, else file)",
    )
    _add_creds_flag(dl, transport_note=False)
    dl.set_defaults(func=_cmd_delete_creds)

    dc = sub.add_parser("discover", help="list MCP servers from installed agent hosts")
    dc.add_argument(
        "--host", action="append", dest="host", metavar="ID", help="filter to this host id (repeatable; default: all)"
    )
    dc.add_argument("--json", action="store_true", help="emit JSON array instead of human table")
    dc.add_argument(
        "--include-env",
        action="store_true",
        dest="include_env",
        help="include raw env values in JSON output (may expose secrets)",
    )
    dc.set_defaults(func=_cmd_discover)

    ns = parser.parse_args(argv)
    try:
        return ns.func(ns)
    except (_bridge.ReauthenticationRequired, _bridge.LoginWontHelp) as exc:
        # Any command that touches a server can hit the auth taxonomy, and an expired
        # credential or a down endpoint is a routine operational condition, not a
        # traceback. Catching the two roots here covers their subclasses and every command
        # added later. Not `Exception`: a KeyError from a real bug must still traceback.
        # The messages carry their own next step — "Run: mcpgen login X", "retry later" —
        # so nothing is appended. `_cmd_login`'s own handler runs first and keeps its
        # formatting.
        print(f"[mcpgen] error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
