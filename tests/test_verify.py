"""Tests for eval_harness.verify — the 5-check contract."""
import ast
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval_harness.verify import (
    check_ast,
    check_signatures,
    check_pii,
    check_roundtrip,
    CheckResult,
)
from eval_harness.manifest import ServerSpec


# ---------------------------------------------------------------------------
# Check 1: AST
# ---------------------------------------------------------------------------


def test_check_ast_pass(tmp_path: Path) -> None:
    """A valid Python module should return status='pass'."""
    module = tmp_path / "good.py"
    module.write_text(
        "from __future__ import annotations\n\ndef foo(x: int) -> int:\n    return x\n",
        encoding="utf-8",
    )
    result = check_ast(module)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_ast_fail(tmp_path: Path) -> None:
    """Broken Python (unclosed parenthesis) should return status='fail' with SyntaxError detail."""
    module = tmp_path / "bad.py"
    module.write_text("def foo( # unclosed\n", encoding="utf-8")
    result = check_ast(module)
    assert result.status == "fail"
    # The detail should contain something from the SyntaxError message
    assert result.detail != "", "detail should not be empty on AST failure"


# ---------------------------------------------------------------------------
# Check 2: Signatures
# ---------------------------------------------------------------------------

# Minimal generated module — Mode B (single-record TypedDict)
_MODULE_B = """\
from __future__ import annotations
from typing import Any, TypedDict, cast
from mcp_client_kit.seam import McpCaller

class User(TypedDict, total=False):
    login: str
    id: int

async def get_me(caller: McpCaller) -> User:
    result = await caller.call('github', 'get_me', {})
    return cast("User", result)

async def list_tools(caller: McpCaller) -> Any:
    result = await caller.call('github', 'list_tools', {})
    return result
"""

_SHAPES_B = {
    "get_me": {"return_model": "User", "fields": {"login": "str", "id": "int"}, "unwrap": []},
    "list_tools": {"return_model": None},
}

# Minimal generated module — Mode C (list TypedDict)
_MODULE_C = """\
from __future__ import annotations
from typing import Any, TypedDict, cast
from mcp_client_kit.seam import McpCaller

class Branch(TypedDict, total=False):
    name: str
    protected: bool

async def list_branches(caller: McpCaller, owner: str, repo: str) -> list[Branch]:
    result = await caller.call('github', 'list_branches', {'owner': owner, 'repo': repo})
    return cast("list[Branch]", result)

async def list_tools(caller: McpCaller) -> Any:
    result = await caller.call('github', 'list_tools', {})
    return result
"""

_SHAPES_C = {
    "list_branches": {
        "return_model": "Branch",
        "return_container": "list",
        "fields": {"name": "str", "protected": "bool"},
        "unwrap": [],
    },
    "list_tools": {"return_model": None},
}

# Module where a shaped tool incorrectly uses -> Any: instead of -> User:
_MODULE_WRONG = """\
from __future__ import annotations
from typing import Any, TypedDict
from mcp_client_kit.seam import McpCaller

class User(TypedDict, total=False):
    login: str
    id: int

async def get_me(caller: McpCaller) -> Any:
    result = await caller.call('github', 'get_me', {})
    return result
"""

_SHAPES_WRONG = {
    "get_me": {"return_model": "User", "fields": {"login": "str", "id": "int"}, "unwrap": []},
}


def test_check_signatures_shaped_mode_b(tmp_path: Path) -> None:
    """Mode B: single-record TypedDict return with return_model='User' should pass."""
    module = tmp_path / "github.py"
    module.write_text(_MODULE_B, encoding="utf-8")

    shapes_path = tmp_path / "github.shapes.json"
    shapes_path.write_text(json.dumps(_SHAPES_B), encoding="utf-8")

    result = check_signatures(module, shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_signatures_shaped_mode_c(tmp_path: Path) -> None:
    """Mode C: list TypedDict return with return_container='list' should pass."""
    module = tmp_path / "github.py"
    module.write_text(_MODULE_C, encoding="utf-8")

    shapes_path = tmp_path / "github.shapes.json"
    shapes_path.write_text(json.dumps(_SHAPES_C), encoding="utf-8")

    result = check_signatures(module, shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_signatures_wrong_return(tmp_path: Path) -> None:
    """When module uses -> Any: but shapes says return_model='User', expect status='fail'."""
    module = tmp_path / "github.py"
    module.write_text(_MODULE_WRONG, encoding="utf-8")

    shapes_path = tmp_path / "github.shapes.json"
    shapes_path.write_text(json.dumps(_SHAPES_WRONG), encoding="utf-8")

    result = check_signatures(module, shapes_path)
    assert result.status == "fail", f"Expected fail, got {result.status!r}: {result.detail}"


# ---------------------------------------------------------------------------
# Check 4: PII
# ---------------------------------------------------------------------------


def test_check_pii_pass(tmp_path: Path) -> None:
    """Shapes with only placeholder probed_args values should return status='pass'."""
    shapes = {
        "list_branches": {
            "return_model": "Branch",
            "probed_args": {"owner": "<example-owner>", "repo": "<example-repo>"},
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_pii_fail_email(tmp_path: Path) -> None:
    """A real email address in probed_args should cause status='fail'."""
    shapes = {
        "send_notification": {
            "return_model": None,
            "probed_args": {"email": "john.doe@example.com"},
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "fail", f"Expected fail, got {result.status!r}: {result.detail}"


def test_check_pii_fail_long_id(tmp_path: Path) -> None:
    """An 8+-digit numeric ID in probed_args should cause status='fail'."""
    shapes = {
        "get_user": {
            "return_model": "User",
            "probed_args": {"id": "12345678"},
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "fail", f"Expected fail, got {result.status!r}: {result.detail}"


# ---------------------------------------------------------------------------
# Check 5: Roundtrip — sidecar lookup
# ---------------------------------------------------------------------------

# Minimal module whose function ignores the caller and returns a stable dict.
# No mcp_client_kit import needed in the module itself — the test only needs
# the package importable (it's a project dep), not used inside the function.
_MODULE_ROUNDTRIP = """\
async def get_me(caller, **kwargs):
    return {"login": "octocat", "id": 1}
"""

_SHAPES_PLACEHOLDER = {
    "get_me": {
        "return_model": "User",
        "fields": {"login": "str", "id": "int"},
        "probed_args": {"owner": "<example-owner>"},
    }
}

_SPEC_FAKE = ServerSpec(
    name="testserver",
    transport="stdio",
    launch="echo hello",
    auth="none",
)


def test_check_roundtrip_no_sidecar_skips(tmp_path: Path) -> None:
    """Without verify sidecar, placeholder args → skip(probed_args_contain_placeholders)."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_PLACEHOLDER), encoding="utf-8")

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.status == "skip", f"Expected skip, got {result.status!r}: {result.detail}"
    assert "placeholder" in result.detail


def test_check_roundtrip_with_sidecar_bypasses_placeholder_guard(tmp_path: Path) -> None:
    """With verify sidecar, real args used → placeholder guard bypassed → roundtrip passes."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_PLACEHOLDER), encoding="utf-8")
    # Sidecar with real (non-placeholder) args keyed by tool name
    (tmp_path / "testserver.verify.json").write_text(
        json.dumps({"get_me": {"owner": "octocat"}}), encoding="utf-8"
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    # Placeholder guard must NOT have fired — any other outcome (pass or fail) is acceptable
    assert not (result.status == "skip" and "placeholder" in result.detail), (
        f"Sidecar should have bypassed placeholder guard, got: {result.status!r} {result.detail!r}"
    )
