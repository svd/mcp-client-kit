"""Tests for eval_harness.manifest — server spec loading."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval_harness.manifest import load_manifest


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "servers.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_seed_defaults_to_empty_list(tmp_path: Path) -> None:
    """A server with no seed field loads with seed == []."""
    path = _write(
        tmp_path,
        '[[server]]\nname = "time"\ntransport = "stdio"\nlaunch = "uvx mcp-server-time"\n',
    )
    (spec,) = load_manifest(path)
    assert spec.seed == []


def test_seed_is_loaded_when_present(tmp_path: Path) -> None:
    """Seed commands are carried through verbatim, in order."""
    path = _write(
        tmp_path,
        '[[server]]\n'
        'name = "memory"\n'
        'transport = "stdio"\n'
        'launch = "npx -y @modelcontextprotocol/server-memory"\n'
        'seed = ["mcpgen call memory create_entities --args \'{}\'", '
        '"mcpgen call memory create_relations --args \'{}\'"]\n',
    )
    (spec,) = load_manifest(path)
    assert spec.seed == [
        "mcpgen call memory create_entities --args '{}'",
        "mcpgen call memory create_relations --args '{}'",
    ]
