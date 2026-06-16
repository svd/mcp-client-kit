"""Generate a Claude Code-style mcpServers JSON config from servers.toml.

The generated file (.mcp.eval.json by default) lets `mcp-kit` resolve HTTP/SSE
servers by name via $MCP_KIT_SERVERS or `--config`.  Stdio entries are included
for completeness but are inert in mcp-kit's name-resolution (it only resolves
entries that carry a `url`).  Bearer tokens are NOT embedded — they stay in env
vars and must be passed via `--bearer` as usual.

Typical use:
    uv run eval-kit gen-config
    MCP_KIT_SERVERS=.mcp.eval.json mcp-kit list deepwiki
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from eval_harness.manifest import ServerSpec, load_manifest

_DEFAULT_MANIFEST = Path("servers/servers.toml")
_DEFAULT_OUT = Path(".mcp.eval.json")


def _spec_to_entry(spec: ServerSpec) -> dict:
    """Convert one ServerSpec to a mcpServers JSON entry."""
    if spec.transport == "stdio":
        parts = shlex.split(spec.launch)
        return {"command": parts[0], "args": parts[1:]}
    # http or sse — bearer/oauth tokens intentionally omitted
    return {"type": spec.transport, "url": spec.launch}


def build_mcp_config(specs: list[ServerSpec]) -> dict:
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
