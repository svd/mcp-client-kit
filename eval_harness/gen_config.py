"""Generate a Claude Code-style mcpServers JSON config from servers.toml.

The generated file (.mcp.eval.json by default) lets `mcpgen` resolve servers
by name via $MCPGEN_SERVERS or `--config`.  Both stdio and HTTP/SSE entries
resolve by name; stdio entries additionally carry their `env` block (if any) to
the spawned child process — `${VAR}` references are expanded from the host
environment at parse time.  This is the mechanism that forwards keys like
`CONTEXT7_API_KEY` to stdio servers without having to pass `--env` on every
command.  Bearer tokens for HTTP/SSE servers are written as a literal
``${VAR}`` placeholder in a ``headers.Authorization`` block; the consumer
(Claude Code / mcpgen) expands the variable at connection time.

Typical use:
    uv run eval-kit gen-config
    MCPGEN_SERVERS=.mcp.eval.json mcpgen list context7
    MCPGEN_SERVERS=.mcp.eval.json mcpgen list deepwiki
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from eval_harness.manifest import ServerSpec, load_manifest

_DEFAULT_MANIFEST = Path("servers/servers.toml")
_DEFAULT_OUT = Path(".mcp.eval.json")


def _spec_to_entry(spec: ServerSpec) -> dict[str, Any]:
    """Convert one ServerSpec to a mcpServers JSON entry."""
    if spec.transport == "stdio":
        parts = shlex.split(spec.launch)
        entry: dict[str, Any] = {"command": parts[0], "args": parts[1:]}
    else:
        # http or sse
        entry = {"type": spec.transport, "url": spec.launch}
        if spec.auth_kind == "bearer" and spec.bearer_env_var:
            entry["headers"] = {"Authorization": f"Bearer ${{{spec.bearer_env_var}}}"}
    if spec.env:
        entry["env"] = spec.env
    return entry


def build_mcp_config(specs: list[ServerSpec]) -> dict[str, Any]:
    """Return a ``{"mcpServers": {...}}`` dict from a list of ServerSpecs."""
    return {"mcpServers": {spec.name: _spec_to_entry(spec) for spec in specs}}


def write_mcp_config(
    out_path: Path | str = _DEFAULT_OUT,
    manifest_path: Path | str = _DEFAULT_MANIFEST,
) -> Path:
    """Load servers.toml, build config, write JSON.  Returns the output path."""
    out_path = Path(out_path)
    specs = load_manifest(manifest_path)
    config = build_mcp_config(specs)
    out_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return out_path
