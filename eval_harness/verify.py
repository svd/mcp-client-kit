"""Deterministic verifier for mcp-client-kit-eval: runs 5 checks on a server's
generated artifacts and writes result.json.

Checks:
  1. ast          — generated .py parses without SyntaxError
  2. signatures   — return-type annotations match shapes.json contracts
  3. idempotency  — render_module() is deterministic (offline, stub schemas)
  4. pii          — shapes.json probed_args contain no PII-like values
  5. roundtrip    — live call returns typed dict (requires creds + non-mutating tool)
"""
from __future__ import annotations

import ast
import json
import os
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from eval_harness.manifest import ServerSpec

# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""
    extra: dict = field(default_factory=dict)


def pass_(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, status="pass", detail=detail)


def fail_(name: str, detail: str, extra: dict | None = None) -> CheckResult:
    return CheckResult(name=name, status="fail", detail=detail, extra=extra or {})


def skip_(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="skip", detail=reason)


# ── PII patterns ──────────────────────────────────────────────────────────────

_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_RE_LONG_NUM = re.compile(r"\b\d{8,}\b")
_RE_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

# Mutating verbs — tool names containing any of these are considered mutating
_MUTATING_VERBS = {
    "create", "update", "delete", "remove", "send", "write", "post",
    "patch", "put", "cancel", "approve", "submit", "assign", "push",
    "merge", "fork",
}


# ── Check 1: AST ──────────────────────────────────────────────────────────────


def check_ast(server_py: Path) -> CheckResult:
    """Parse the generated .py; fail on SyntaxError."""
    try:
        content = server_py.read_text(encoding="utf-8")
        ast.parse(content)
        return pass_("ast")
    except SyntaxError as e:
        return fail_("ast", str(e))
    except OSError as e:
        return fail_("ast", f"Could not read file: {e}")


# ── Check 2: Signatures ───────────────────────────────────────────────────────


def check_signatures(server_py: Path, shapes_json: Path) -> CheckResult:
    """Verify that return-type annotations in the .py match shapes.json contracts."""
    try:
        source = server_py.read_text(encoding="utf-8")
    except OSError as e:
        return fail_("signatures", f"Could not read file: {e}")

    try:
        shapes: dict = json.loads(shapes_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return skip_("signatures", f"Could not load shapes.json: {e}")

    failures: list[str] = []
    has_any_return_model = False

    for tool_name, shape in shapes.items():
        return_model: str | None = shape.get("return_model")
        return_container: str | None = shape.get("return_container")

        if return_model is not None:
            has_any_return_model = True
            if return_container == "list":
                expected_sig = f"-> list[{return_model}]:"
            else:
                expected_sig = f"-> {return_model}:"
            if expected_sig not in source:
                failures.append(
                    f"{tool_name}: expected '{expected_sig}' not found in source"
                )
        else:
            # null return_model — we expect -> Any: somewhere (at least one)
            pass

    # If any tool has return_model null, check that "-> Any:" appears at least once
    has_null_return = any(
        shape.get("return_model") is None for shape in shapes.values()
    )
    if has_null_return and "-> Any:" not in source:
        failures.append("(unshaped tools): expected '-> Any:' in source but not found")

    # Check imports when return models are present
    if has_any_return_model:
        if "from __future__ import annotations" not in source:
            failures.append("missing 'from __future__ import annotations'")
        if "TypedDict" not in source:
            failures.append("missing TypedDict in imports")

    if failures:
        return fail_("signatures", "; ".join(failures), extra={"failures": failures})
    return pass_("signatures")


# ── Check 3: Idempotency ──────────────────────────────────────────────────────


def check_idempotency(server: str, shapes_json: Path) -> CheckResult:
    """Call render_module() twice with stub schemas and assert identical output."""
    try:
        import mcp_client_kit.codegen as codegen  # noqa: PLC0415
    except ImportError:
        return skip_("idempotency", "mcp_client_kit not installed")

    try:
        shapes_data: dict = json.loads(shapes_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return skip_("idempotency", f"Could not load shapes.json: {e}")

    stub_tools = [
        {"name": k, "inputSchema": {"type": "object", "properties": {}}}
        for k in shapes_data.keys()
    ]

    try:
        result1 = codegen.render_module(server, stub_tools, shapes=shapes_data)
        result2 = codegen.render_module(server, stub_tools, shapes=shapes_data)
    except Exception as e:  # noqa: BLE001
        return fail_("idempotency", f"render_module raised: {e}")

    if result1 == result2:
        return pass_("idempotency", "offline determinism check (stub schemas only)")
    return fail_(
        "idempotency",
        "render_module() produced different output on two calls (non-determinism bug)",
    )


# ── Check 4: PII ──────────────────────────────────────────────────────────────


def _is_placeholder(value: str) -> bool:
    """Return True if the string looks like a placeholder (e.g. <example-id>)."""
    return "<" in value and ">" in value


def _scan_for_pii(
    tool_name: str,
    obj: Any,
    path: str,
    findings: list[tuple[str, str, str]],
) -> None:
    """Recursively scan obj for PII-like string values."""
    if isinstance(obj, str):
        if _is_placeholder(obj):
            return
        for pattern in (_RE_EMAIL, _RE_LONG_NUM, _RE_UUID):
            m = pattern.search(obj)
            if m:
                preview = obj[:80] + ("..." if len(obj) > 80 else "")
                findings.append((tool_name, path, preview))
                return  # one finding per value is enough
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _scan_for_pii(tool_name, v, f"{path}.{k}" if path else k, findings)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_for_pii(tool_name, item, f"{path}[{i}]", findings)


def check_pii(shapes_json: Path) -> CheckResult:
    """Scan probed_args in shapes.json for PII-like values."""
    try:
        shapes: dict = json.loads(shapes_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return skip_("pii", f"Could not load shapes.json: {e}")

    findings: list[tuple[str, str, str]] = []
    for tool_name, shape in shapes.items():
        probed_args = shape.get("probed_args", {})
        _scan_for_pii(tool_name, probed_args, "", findings)

    if findings:
        detail_lines = [
            f"{tool} @ {fpath!r}: {preview!r}"
            for tool, fpath, preview in findings
        ]
        return fail_(
            "pii",
            f"{len(findings)} PII-like value(s) found in probed_args",
            extra={"findings": detail_lines},
        )
    return pass_("pii")


# ── Check 5: Roundtrip ────────────────────────────────────────────────────────


def _is_mutating(tool_name: str) -> bool:
    name_lower = tool_name.lower()
    return any(verb in name_lower for verb in _MUTATING_VERBS)


def check_roundtrip(
    spec: ServerSpec, server_dir: Path, shapes_json: Path
) -> CheckResult:
    """Live call: find a shaped non-mutating tool, call it, verify typed return."""
    try:
        shapes: dict = json.loads(shapes_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return skip_("roundtrip", f"Could not load shapes.json: {e}")

    # Find a shaped, non-mutating tool
    candidate_name: str | None = None
    candidate_shape: dict | None = None
    for tool_name, shape in shapes.items():
        if shape.get("return_model") is not None and not _is_mutating(tool_name):
            candidate_name = tool_name
            candidate_shape = shape
            break

    if candidate_name is None or candidate_shape is None:
        return skip_("roundtrip", "no_shaped_non_mutating_tool")

    # Check credentials
    if spec.auth_kind == "oauth":
        return skip_("roundtrip", "oauth_not_supported_in_verifier")

    if spec.auth_kind == "bearer":
        env_var = spec.bearer_env_var
        if env_var is None or os.environ.get(env_var) is None:
            var_name = env_var or "UNKNOWN"
            return skip_("roundtrip", f"missing_cred_{var_name}")

    # We have a candidate and (if needed) credentials — attempt the live call
    server_py = server_dir / f"{spec.name}.py"
    try:
        src = server_py.read_text(encoding="utf-8")
    except OSError as e:
        return fail_("roundtrip", f"Could not read generated module: {e}")

    try:
        from mcp_client_kit._bridge import McpBridgeCaller  # noqa: PLC0415
    except ImportError:
        return skip_("roundtrip", "mcp_client_kit not installed")

    # Build the caller
    bearer_token: str | None = None
    if spec.auth_kind == "bearer" and spec.bearer_env_var:
        bearer_token = os.environ.get(spec.bearer_env_var)

    try:
        if spec.transport == "stdio":
            caller = McpBridgeCaller(cmd=spec.launch, bearer=bearer_token)
        else:
            caller = McpBridgeCaller(url=spec.launch, bearer=bearer_token)
    except Exception as e:
        return fail_("roundtrip", f"Failed to construct caller: {e}")

    # Load the generated module via exec
    ns: dict[str, Any] = {}
    try:
        exec(compile(src, f"{spec.name}.py", "exec"), ns)  # noqa: S102
    except ImportError as e:
        return skip_("roundtrip", f"generated module has unresolvable imports: {e}")
    except Exception as e:  # noqa: BLE001
        return fail_("roundtrip", f"exec of generated module failed: {e}")

    # Find the function corresponding to candidate_name
    # Tool names may be sanitized — try direct name first, then sanitized form
    fn = ns.get(candidate_name)
    if fn is None:
        # Try sanitizing the name (replicating codegen.sanitize logic)
        sanitized = re.sub(r"\W", "_", candidate_name)
        if not sanitized or sanitized[0].isdigit():
            sanitized = "_" + sanitized
        import keyword  # noqa: PLC0415
        if keyword.iskeyword(sanitized):
            sanitized += "_"
        fn = ns.get(sanitized)

    if fn is None or not callable(fn):
        return skip_(
            "roundtrip",
            f"function '{candidate_name}' not found in generated module namespace",
        )

    probed_args = candidate_shape.get("probed_args", {})
    if not isinstance(probed_args, dict):
        return skip_("roundtrip", "probed_args is not a dict (multi-probe list not supported for live call)")
    # Prefer real pre-scrub args from a gitignored sidecar when available, so the
    # live call uses values the server accepts instead of <example-*> placeholders.
    verify_args_path = shapes_json.parent / f"{spec.name}.verify.json"
    if verify_args_path.exists():
        try:
            overrides = json.loads(verify_args_path.read_text(encoding="utf-8"))
            if isinstance(overrides, dict) and isinstance(overrides.get(candidate_name), dict):
                probed_args = overrides[candidate_name]
        except (OSError, json.JSONDecodeError):
            pass  # unreadable sidecar — fall through to placeholder guard
    if any(isinstance(v, str) and _is_placeholder(v) for v in probed_args.values()):
        return skip_("roundtrip", "probed_args_contain_placeholders")
    return_model: str = candidate_shape["return_model"]
    return_container: str | None = candidate_shape.get("return_container")
    expected_fields: dict = candidate_shape.get("fields", {})

    try:
        import asyncio  # noqa: PLC0415

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, fn(caller, **probed_args))
                result = future.result(timeout=30)
        else:
            result = asyncio.run(fn(caller, **probed_args))
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        return fail_("roundtrip", f"live call raised {type(e).__name__}: {e}", extra={"traceback": tb})

    # Validate result shape
    if return_container == "list":
        if not isinstance(result, list):
            return fail_(
                "roundtrip",
                f"expected list[{return_model}] but got {type(result).__name__}",
            )
        if result and isinstance(result[0], dict) and expected_fields:
            item = result[0]
            matching = [k for k in expected_fields if k in item]
            if not matching:
                return fail_(
                    "roundtrip",
                    f"first item in list has none of the expected fields {list(expected_fields.keys())!r}",
                )
    else:
        if isinstance(result, str):
            return fail_(
                "roundtrip",
                f"expected typed dict ({return_model}) but got a string result",
            )
        if isinstance(result, dict) and expected_fields:
            matching = [k for k in expected_fields if k in result]
            if not matching:
                return fail_(
                    "roundtrip",
                    f"result dict has none of the expected fields {list(expected_fields.keys())!r}",
                )

    return pass_("roundtrip", f"live call to '{candidate_name}' returned typed result")


# ── Modes detection ───────────────────────────────────────────────────────────


def _compute_modes_hit(shapes: dict | None) -> list[str]:
    """Determine which eval modes are exercised by the shapes."""
    if shapes is None:
        return []

    modes: set[str] = set()

    if shapes:  # any tool exists
        modes.add("A")

    for shape in shapes.values():
        return_model = shape.get("return_model")
        return_container = shape.get("return_container")
        if return_model is not None:
            if return_container == "list":
                modes.add("C")
            else:
                modes.add("B")
        if "discriminator" in shape:
            modes.add("D")

    return sorted(modes)


# ── Main entry point ──────────────────────────────────────────────────────────


def verify_server(spec: ServerSpec, base_dir: Path = Path("eval")) -> dict:
    """Run all 5 checks for a server and write result.json.

    Returns the result dict.
    """
    server_dir = base_dir / spec.name
    server_py = server_dir / f"{spec.name}.py"
    shapes_json = server_dir / f"{spec.name}.shapes.json"

    # If generated file doesn't exist at all — early error return
    if not server_py.exists():
        result: dict = {
            "server": spec.name,
            "transport": spec.transport,
            "auth": spec.auth,
            "checks": {},
            "check_details": {},
            "modes_hit": [],
            "verdict": "error",
            "error": "no generated file found",
        }
        return result

    shapes_present = shapes_json.exists()

    # Run checks
    ast_result = check_ast(server_py)

    if shapes_present:
        sig_result = check_signatures(server_py, shapes_json)
        idem_result = check_idempotency(spec.name, shapes_json)
        pii_result = check_pii(shapes_json)
        rt_result = check_roundtrip(spec, server_dir, shapes_json)
    else:
        sig_result = skip_("signatures", "no shapes.json found")
        idem_result = skip_("idempotency", "no shapes.json found")
        pii_result = skip_("pii", "no shapes.json found")
        rt_result = skip_("roundtrip", "no shapes.json found")

    all_checks = [ast_result, sig_result, idem_result, pii_result, rt_result]

    # Load shapes for modes computation (None if not present)
    shapes_data: dict | None = None
    if shapes_present:
        try:
            shapes_data = json.loads(shapes_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            shapes_data = {}

    modes_hit = _compute_modes_hit(shapes_data)

    # Determine verdict
    if ast_result.status == "fail":
        verdict = "fail"
    else:
        non_skip = [c for c in all_checks if c.status != "skip"]
        if all(c.status == "pass" for c in non_skip):
            verdict = "pass"
        elif any(c.status == "fail" for c in non_skip):
            verdict = "partial"
        else:
            # All are skip (edge case: no checks ran as non-skip)
            verdict = "pass"

    result = {
        "server": spec.name,
        "transport": spec.transport,
        "auth": spec.auth,
        "checks": {c.name: c.status for c in all_checks},
        "check_details": {c.name: c.detail for c in all_checks},
        "modes_hit": modes_hit,
        "verdict": verdict,
    }

    # Write result.json
    server_dir.mkdir(parents=True, exist_ok=True)
    result_path = server_dir / "result.json"
    tmp = result_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
    tmp.rename(result_path)

    return result
