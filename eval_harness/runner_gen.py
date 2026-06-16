"""Generate transport-aware sample runner scripts for evaluated MCP servers."""
from __future__ import annotations

import json
from pathlib import Path
from string import Template

from eval_harness.manifest import ServerSpec

# Template key: (transport, auth_kind)
_TEMPLATE_KEYS = {
    ("stdio", "none"): "stdio",
    ("http", "none"): "http_public",
    ("sse", "none"): "http_public",   # SSE no-auth uses same template as HTTP no-auth
    ("http", "bearer"): "http_bearer",
    ("sse", "bearer"): "http_bearer",
    ("http", "oauth"): "http_oauth",
    ("sse", "oauth"): "http_oauth",
    ("stdio", "oauth"): "stdio",      # stdio+oauth is unusual but default to stdio template
    ("stdio", "bearer"): "stdio",
}

def _load_template(key: str) -> str:
    tpl_path = Path(__file__).parent / "runner_templates" / f"{key}.py"
    return tpl_path.read_text(encoding="utf-8")

def _find_shaped_tools(shapes: dict) -> list[tuple[str, str, bool]]:
    """Return list of (tool_name, return_annotation, is_list) for shaped tools."""
    result = []
    for tool_name, shape in shapes.items():
        model = shape.get("return_model")
        if model:
            is_list = shape.get("return_container") == "list"
            result.append((tool_name, model, is_list))
    return result

def _find_unshaped_tool(shapes: dict) -> str | None:
    """Return first tool with return_model=null."""
    for tool_name, shape in shapes.items():
        if not shape.get("return_model"):
            return tool_name
    return None

def generate_runner(
    spec: ServerSpec,
    server_dir: Path,
    shapes: dict | None = None,
) -> str:
    """Generate run.py content for a server and write it to server_dir/run.py.

    Returns the generated content.
    """
    # Select template
    key = (spec.transport, spec.auth_kind)
    template_key = _TEMPLATE_KEYS.get(key, "http_public")
    template_src = _load_template(template_key)

    # Gather substitution values
    shaped = _find_shaped_tools(shapes or {})
    unshaped = _find_unshaped_tool(shapes or {})

    # Build the demo calls block (printed as code comment lines + actual calls)
    # The templates use $demo_calls placeholder
    demo_calls = _build_demo_calls(spec.name, shaped, unshaped, shapes or {})

    subs = {
        "server_name": spec.name,
        "server_name_upper": spec.name.upper().replace("-", "_"),
        "launch": spec.launch,
        "bearer_env_var": spec.bearer_env_var or "BEARER_TOKEN",
        "demo_calls": demo_calls,
        "module_name": spec.name.replace("-", "_"),  # the generated module is importable as this name
    }

    content = Template(template_src).safe_substitute(subs)

    out_path = server_dir / "run.py"
    out_path.write_text(content, encoding="utf-8")
    return content


def _build_demo_calls(server_name: str, shaped: list, unshaped: str | None, shapes: dict) -> str:
    """Build the demo-calls block: comment + await call + print."""
    lines = []
    module_name = server_name.replace("-", "_")

    # One unshaped call (→ Any)
    if unshaped:
        args_dict = shapes[unshaped].get("probed_args", {})
        if isinstance(args_dict, list):
            args_dict = args_dict[0] if args_dict else {}
        args_str = _kwargs_str(args_dict)
        fn_name = unshaped.replace("-", "_")
        lines.append(f"    # {unshaped} → Any")
        lines.append(f"    result = await {module_name}.{fn_name}(caller{args_str})")
        lines.append(f'    print(f"{unshaped}: {{type(result).__name__}}")')
        lines.append("")

    # Up to 2 shaped calls
    for tool_name, model, is_list in shaped[:2]:
        args_dict = shapes[tool_name].get("probed_args", {})
        if isinstance(args_dict, list):
            args_dict = args_dict[0] if args_dict else {}
        args_str = _kwargs_str(args_dict)
        ret_type = f"list[{model}]" if is_list else model
        fn_name = tool_name.replace("-", "_")
        lines.append(f"    # {tool_name} → {ret_type}")
        lines.append(f"    result = await {module_name}.{fn_name}(caller{args_str})")
        if is_list:
            lines.append(f'    print(f"{tool_name}: {{len(result)}} item(s)")')
        else:
            lines.append(f'    print(f"{tool_name}: {{result}}")')
        lines.append("")

    if not lines:
        lines.append("    # (no shaped tools — add manual calls here)")
        lines.append(f"    pass")

    return "\n".join(lines)


def _kwargs_str(args: dict) -> str:
    """Convert a probed_args dict to a kwargs string for a function call."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        parts.append(f"{k}={v!r}")
    return ", " + ", ".join(parts)
