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
    check_idempotency,
    verify_server,
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
from mcpgen.seam import McpCaller

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
from mcpgen.seam import McpCaller

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
from mcpgen.seam import McpCaller

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


def test_check_pii_pass_prefixed_public_id(tmp_path: Path) -> None:
    """Prefixed public bibliographic ids are functional values, so status='pass'."""
    shapes = {
        "research_read_paper": {
            "return_model": "Paper",
            "probed_args": {
                "paperId": "pmid:34515826",
                "doi": "doi:10.1038/s41586-021-03819-2",
                "preprint": "arXiv:2103.00020",
            },
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_pii_fail_unprefixed_public_id(tmp_path: Path) -> None:
    """The same id without its public-id prefix is indistinguishable from an account
    id, so status='fail'."""
    shapes = {
        "research_read_paper": {
            "return_model": "Paper",
            "probed_args": {"paperId": "34515826"},
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "fail", f"Expected fail, got {result.status!r}: {result.detail}"


def test_check_pii_pass_placeholder_uuid(tmp_path: Path) -> None:
    """Synthetic repeated-digit UUIDs are scrub output, not PII, so status='pass'."""
    shapes = {
        "get_issue": {
            "return_model": "Issue",
            "probed_args": {
                "id": "11111111-2222-4333-8444-555555555559",
                "teamId": "11111111-2222-3333-4444-555555555555",
                "url": "https://example.com/issue/66666666-7777-8888-9999-000000000000",
            },
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_pii_pass_placeholder_uuid_dashless(tmp_path: Path) -> None:
    """Dashless 32-hex placeholders (Notion id format) are scrub output, so status='pass'."""
    shapes = {
        "notion-fetch": {
            "return_model": "Page",
            "probed_args": {"id": "00000000000000000000000000000000"},
        },
        "get-comments": {
            "return_model": "Comment",
            "probed_args": {"page_id": "11111111111111111111111111111111"},
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_pii_fail_real_dashless_id(tmp_path: Path) -> None:
    """A real (varied) dashless 32-hex id is not exempted, so status='fail'.

    The value is all digits, exactly like the all-zeros placeholder it must be
    told apart from — being 32 chars long does not buy the exemption; only the
    repeated-character shape does.
    """
    shapes = {
        "notion-fetch": {
            "return_model": "Page",
            "probed_args": {"id": "10293847560192837465019283746501"},
        },
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "fail", f"Expected fail, got {result.status!r}: {result.detail}"


def test_check_pii_git_sha_unaffected(tmp_path: Path) -> None:
    """A 40-char git sha is not a 32-hex placeholder; the dashless rule leaves it alone."""
    from eval_harness.verify import _RE_UUID_DASHLESS

    sha = "9c1e2ab3f45d6789e0123456789abcdef0123456"
    assert _RE_UUID_DASHLESS.search(sha) is None

    shapes = {
        "get_commit": {"return_model": "Commit", "probed_args": {"sha": sha}},
    }
    shapes_path = tmp_path / "server.shapes.json"
    shapes_path.write_text(json.dumps(shapes), encoding="utf-8")

    result = check_pii(shapes_path)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"


def test_check_pii_fail_real_uuid(tmp_path: Path) -> None:
    """A real UUID in probed_args still causes status='fail'."""
    shapes = {
        "get_issue": {
            "return_model": "Issue",
            "probed_args": {"id": "3f2a91c4-7b6d-4e8f-9a1b-c5d0e7f28a63"},
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
# No mcpgen import needed in the module itself — the test only needs
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


# ---------------------------------------------------------------------------
# Version stamping
# ---------------------------------------------------------------------------


def test_verify_server_stamps_versions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """result.json records which engine/skill produced the artifacts."""
    server_dir = tmp_path / "testserver"
    server_dir.mkdir()
    (server_dir / "testserver.py").write_text("x = 1\n", encoding="utf-8")

    stamp = {"engine": "0.7.0", "skill_ref": "v0.7.0", "skill_path": "/plugins/kit"}
    monkeypatch.setattr("eval_harness.verify.runtime_versions", lambda: stamp)

    result = verify_server(_SPEC_FAKE, base_dir=tmp_path)

    assert result["versions"] == stamp
    on_disk = json.loads((server_dir / "result.json").read_text(encoding="utf-8"))
    assert on_disk["versions"] == stamp


def test_verify_server_stamps_versions_when_module_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The early error return carries versions too — a failed run is still attributable."""
    (tmp_path / "testserver").mkdir()

    stamp = {"engine": "0.7.0", "skill_ref": None, "skill_path": None}
    monkeypatch.setattr("eval_harness.verify.runtime_versions", lambda: stamp)

    result = verify_server(_SPEC_FAKE, base_dir=tmp_path)

    assert result["verdict"] == "error"
    assert result["versions"] == stamp


# ---------------------------------------------------------------------------
# Check 3: Idempotency — real tool schemas vs stub fallback
# ---------------------------------------------------------------------------

_SHAPES_FOR_IDEM = {
    "get_current_time": {
        "return_model": "CurrentTime",
        "fields": {"timezone": "str", "datetime": "str"},
        "probed_args": {"timezone": "UTC"},
    }
}

_MCPGEN_JSON = {
    "format_version": 1,
    "server": "testserver",
    "tools": {
        "get_current_time": {
            "description": "Get the current time in a timezone",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "enum": ["UTC", "Europe/Minsk"]},
                },
                "required": ["timezone"],
            },
        }
    },
}


def test_check_idempotency_uses_real_schemas_when_mcpgen_json_present(
    tmp_path: Path,
) -> None:
    """With <server>.mcpgen.json on disk, the check renders from real tool schemas."""
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_FOR_IDEM), encoding="utf-8")
    (tmp_path / "testserver.mcpgen.json").write_text(
        json.dumps(_MCPGEN_JSON), encoding="utf-8"
    )

    result = check_idempotency("testserver", shapes)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"
    assert "real tool schemas" in result.detail, result.detail


def test_check_idempotency_falls_back_to_stubs_without_mcpgen_json(
    tmp_path: Path,
) -> None:
    """Without the mcpgen sidecar the check degrades to stub schemas and says so."""
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_FOR_IDEM), encoding="utf-8")

    result = check_idempotency("testserver", shapes)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"
    assert "stub schemas" in result.detail, result.detail


# ---------------------------------------------------------------------------
# Check 5: Roundtrip — skip reasons distinguish design from coverage gap
# ---------------------------------------------------------------------------


def test_check_roundtrip_all_prose_tools_skip_by_design(tmp_path: Path) -> None:
    """Every tool returns unstructured text → skip reason says it's by design."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(
        json.dumps(
            {
                "search": {"return_model": None, "_observed_shape": "str"},
                "fetch": {"return_model": None, "_observed_shape": "str"},
            }
        ),
        encoding="utf-8",
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.status == "skip", f"Expected skip, got {result.status!r}: {result.detail}"
    assert result.detail == "no_shaped_tool_by_design", result.detail


def test_check_roundtrip_only_mutating_shaped_tools_is_a_coverage_gap(
    tmp_path: Path,
) -> None:
    """Shaped tools exist but all are mutating → distinct, non-'by design' reason."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(
        json.dumps(
            {
                "create_entity": {
                    "return_model": "Entity",
                    "fields": {"id": "str"},
                    "probed_args": {"name": "x"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.status == "skip", f"Expected skip, got {result.status!r}: {result.detail}"
    assert result.detail == "only_mutating_shaped_tools", result.detail


def test_check_roundtrip_inconclusive_probes_are_not_by_design(tmp_path: Path) -> None:
    """Quota/auth-blocked probes must not be reported as prose-only by design."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(
        json.dumps(
            {
                "search": {
                    "return_model": None,
                    "_observed_shape": "str",
                    "_probe_status": "inconclusive",
                },
            }
        ),
        encoding="utf-8",
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.status == "skip", f"Expected skip, got {result.status!r}: {result.detail}"
    assert result.detail.startswith("probe_inconclusive"), result.detail


def test_check_roundtrip_partial_inconclusive_still_flagged(tmp_path: Path) -> None:
    """One blocked probe is enough — the prose-only claim is no longer establishable."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(
        json.dumps(
            {
                "search": {"return_model": None, "_observed_shape": "str"},
                "fetch": {"return_model": None, "_probe_status": "inconclusive"},
            }
        ),
        encoding="utf-8",
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.detail.startswith("probe_inconclusive"), result.detail


def test_check_roundtrip_empty_shapes_is_not_by_design(tmp_path: Path) -> None:
    """An empty shapes.json proves nothing about the server's return types."""
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text("{}", encoding="utf-8")

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.status == "skip", f"Expected skip, got {result.status!r}: {result.detail}"
    assert result.detail == "shapes_json_empty", result.detail


def test_check_idempotency_reports_unusable_mcpgen_json_distinctly(
    tmp_path: Path,
) -> None:
    """A corrupt sidecar must not be reported as an absent one — result.json is ground truth."""
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_FOR_IDEM), encoding="utf-8")
    (tmp_path / "testserver.mcpgen.json").write_text("{not json", encoding="utf-8")

    result = check_idempotency("testserver", shapes)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"
    assert "unusable" in result.detail, result.detail
    assert "no <server>.mcpgen.json on disk" not in result.detail, result.detail


def test_check_idempotency_reports_unusable_when_tools_is_not_a_dict(
    tmp_path: Path,
) -> None:
    """A structurally wrong `tools` key is present-but-unusable, not missing."""
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_FOR_IDEM), encoding="utf-8")
    (tmp_path / "testserver.mcpgen.json").write_text(
        json.dumps({"format_version": 1, "tools": ["get_current_time"]}), encoding="utf-8"
    )

    result = check_idempotency("testserver", shapes)
    assert "unusable" in result.detail, result.detail


def test_check_roundtrip_inconclusive_shaped_tool_is_flagged(tmp_path: Path) -> None:
    """A shaped-but-inconclusive tool must not be treated as a valid live candidate.

    `check_signatures` already refuses to trust such an entry (verify.py); roundtrip
    must agree, or result.json carries two contradictory readings of one shape.
    """
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(
        json.dumps(
            {
                "get_me": {
                    "return_model": "User",
                    "fields": {"login": "str"},
                    "probed_args": {"owner": "octocat"},
                    "_probe_status": "inconclusive",
                }
            }
        ),
        encoding="utf-8",
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert result.status == "skip", f"Expected skip, got {result.status!r}: {result.detail}"
    assert result.detail.startswith("probe_inconclusive"), result.detail


def test_check_idempotency_flags_partially_malformed_tool_specs(tmp_path: Path) -> None:
    """Dropping malformed tool entries silently would overstate the check's coverage."""
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(json.dumps(_SHAPES_FOR_IDEM), encoding="utf-8")
    payload = {
        "format_version": 1,
        "tools": {
            "get_current_time": _MCPGEN_JSON["tools"]["get_current_time"],
            "broken_tool": "not-a-dict",
        },
    }
    (tmp_path / "testserver.mcpgen.json").write_text(json.dumps(payload), encoding="utf-8")

    result = check_idempotency("testserver", shapes)
    assert result.status == "pass", f"Expected pass, got {result.status!r}: {result.detail}"
    assert "real tool schemas" in result.detail, result.detail
    assert "1 malformed" in result.detail, result.detail


def test_verify_server_writes_result_json_when_module_missing(tmp_path: Path) -> None:
    """The error path must still persist result.json — the analyze stage quotes it."""
    spec = ServerSpec(name="ghost", transport="stdio", launch="echo hi", auth="none")
    (tmp_path / "ghost").mkdir()

    result = verify_server(spec, base_dir=tmp_path)

    written = tmp_path / "ghost" / "result.json"
    assert written.exists(), "verify_server returned verdict=error without writing result.json"
    assert json.loads(written.read_text(encoding="utf-8")) == result
    assert result["verdict"] == "error"


def test_verify_server_gap_skip_downgrades_verdict(tmp_path: Path) -> None:
    """A server that established nothing must not report a clean pass.

    An empty shapes.json makes every shape-dependent check skip; excluding skips
    from the verdict would otherwise render that as ✅ pass.
    """
    spec = ServerSpec(name="hollow", transport="stdio", launch="echo hi", auth="none")
    d = tmp_path / "hollow"
    d.mkdir()
    (d / "hollow.py").write_text(
        "from typing import Any\n\nasync def noop(caller: Any) -> Any:\n    return None\n",
        encoding="utf-8",
    )
    (d / "hollow.shapes.json").write_text("{}", encoding="utf-8")

    result = verify_server(spec, base_dir=tmp_path)
    assert result["checks"]["roundtrip"] == "skip"
    assert result["check_details"]["roundtrip"] == "shapes_json_empty"
    assert result["verdict"] == "partial", (
        f"a declared coverage gap must not read as pass, got {result['verdict']!r}"
    )


def test_verify_server_inconclusive_probe_downgrades_verdict(tmp_path: Path) -> None:
    """Quota/auth-blocked probes are a gap, so the verdict must not be pass."""
    spec = ServerSpec(name="blocked", transport="stdio", launch="echo hi", auth="none")
    d = tmp_path / "blocked"
    d.mkdir()
    (d / "blocked.py").write_text(
        "from typing import Any\n\nasync def search(caller: Any) -> Any:\n    return None\n",
        encoding="utf-8",
    )
    (d / "blocked.shapes.json").write_text(
        json.dumps({"search": {"return_model": None, "_probe_status": "inconclusive"}}),
        encoding="utf-8",
    )

    result = verify_server(spec, base_dir=tmp_path)
    assert result["verdict"] == "partial", result["verdict"]


def test_verify_server_by_design_skip_still_passes(tmp_path: Path) -> None:
    """A genuine N/A must NOT be downgraded — only gaps are."""
    spec = ServerSpec(name="prose", transport="stdio", launch="echo hi", auth="none")
    d = tmp_path / "prose"
    d.mkdir()
    (d / "prose.py").write_text(
        "from typing import Any\n\nasync def search(caller: Any) -> Any:\n    return None\n",
        encoding="utf-8",
    )
    (d / "prose.shapes.json").write_text(
        json.dumps({"search": {"return_model": None, "_observed_shape": "str"}}),
        encoding="utf-8",
    )

    result = verify_server(spec, base_dir=tmp_path)
    assert result["check_details"]["roundtrip"] == "no_shaped_tool_by_design"
    assert result["verdict"] == "pass", result["verdict"]


def test_check_roundtrip_uses_healthy_candidate_despite_other_inconclusive_tools(
    tmp_path: Path,
) -> None:
    """One blocked probe must not forfeit roundtrip coverage from a healthy tool.

    Inconclusive entries are excluded from candidate selection; the gap is only
    reported when no usable candidate survives.
    """
    (tmp_path / "testserver.py").write_text(_MODULE_ROUNDTRIP, encoding="utf-8")
    shapes = tmp_path / "testserver.shapes.json"
    shapes.write_text(
        json.dumps(
            {
                "blocked_tool": {
                    "return_model": "Thing",
                    "fields": {"id": "str"},
                    "probed_args": {},
                    "_probe_status": "inconclusive",
                },
                "get_me": {
                    "return_model": "User",
                    "fields": {"login": "str"},
                    "probed_args": {"owner": "octocat"},
                },
            }
        ),
        encoding="utf-8",
    )

    result = check_roundtrip(_SPEC_FAKE, tmp_path, shapes)
    assert not result.detail.startswith("probe_inconclusive"), (
        f"a healthy candidate existed but roundtrip was abandoned: {result.detail}"
    )
    assert result.status == "pass", f"{result.status}: {result.detail}"


def test_verify_server_missing_shapes_is_a_gap_not_a_pass(tmp_path: Path) -> None:
    """Absent shapes.json established nothing, same as an empty one.

    Treating absence as a neutral N/A while `{}` downgrades would let the worse
    of the two outcomes report the better verdict.
    """
    spec = ServerSpec(name="noshapes", transport="stdio", launch="echo hi", auth="none")
    d = tmp_path / "noshapes"
    d.mkdir()
    (d / "noshapes.py").write_text(
        "from typing import Any\n\nasync def noop(caller: Any) -> Any:\n    return None\n",
        encoding="utf-8",
    )

    result = verify_server(spec, base_dir=tmp_path)
    assert result["checks"]["roundtrip"] == "skip"
    assert result["verdict"] == "partial", result["verdict"]


def test_probe_inconclusive_detail_states_no_cause() -> None:
    """_probe_status carries no cause, so neither check may name one.

    The marker is a single opaque flag: a 404 "no such object", an expired
    token and an exhausted quota all reach it identically. Naming one is a
    diagnosis the run did not observe, and it is committed into result.json.
    """
    import inspect

    from eval_harness import verify as verify_mod

    source = inspect.getsource(verify_mod)
    for line in source.splitlines():
        if "probe_inconclusive:" not in line:
            continue
        lowered = line.lower()
        assert "quota" not in lowered and "auth" not in lowered, (
            f"probe_inconclusive detail names an unobserved cause: {line.strip()}"
        )


# ── Roundtrip retry ──────────────────────────────────────────────────────────


class _Boom(RuntimeError):
    """A vendor failure re-raised with a status only in its message."""


def _stub_fn(script: list[object]):
    """Build an async wrapper-shaped callable that plays `script` in order."""
    calls: list[int] = []

    async def fn(_caller: object, **_kwargs: object) -> object:
        calls.append(1)
        item = script[len(calls) - 1]
        if isinstance(item, BaseException):
            raise item
        return item

    return fn, calls


def test_retry_recovers_from_transient_5xx(monkeypatch) -> None:
    """Two 503s then a result is a pass, not a wrapper defect."""
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, calls = _stub_fn(
        [
            _Boom("Request failed with status code 503"),
            _Boom("Request failed with status code 503"),
            {"ok": True},
        ]
    )
    result, attempts = v._call_with_retry(fn, object(), {})
    assert result == {"ok": True}
    assert attempts == 3
    assert len(calls) == 3


def test_retry_does_not_widen_to_non_transient_errors(monkeypatch) -> None:
    """A 404 is a real finding: fail on the first attempt, no retry."""
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, calls = _stub_fn([_Boom("Request failed with status code 404")])
    with pytest.raises(_Boom):
        v._call_with_retry(fn, object(), {})
    assert len(calls) == 1, "a 404 must not be retried"


def test_retry_gives_up_after_three_attempts(monkeypatch) -> None:
    """A persistently rate-limited host still fails — bounded, not forever."""
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, calls = _stub_fn([_Boom("429 Too Many Requests")] * 5)
    with pytest.raises(_Boom):
        v._call_with_retry(fn, object(), {})
    assert len(calls) == 3


def test_retry_never_re_calls_on_an_error_shaped_result(monkeypatch) -> None:
    """A returned value — even an isError payload — is never retried."""
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, calls = _stub_fn([{"isError": True, "content": "503 upstream"}])
    result, attempts = v._call_with_retry(fn, object(), {})
    assert result == {"isError": True, "content": "503 upstream"}
    assert attempts == 1
    assert len(calls) == 1, "an error-shaped result is a finding, not a hiccup"


def test_retry_covers_per_attempt_timeouts(monkeypatch) -> None:
    """A bounded attempt that times out may be transient — retry it once."""
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, calls = _stub_fn([TimeoutError("attempt exceeded 30s"), {"ok": True}])
    result, attempts = v._call_with_retry(fn, object(), {})
    assert result == {"ok": True}
    assert attempts == 2
    assert len(calls) == 2


def test_retryable_classification_prefers_a_carried_status() -> None:
    """An exception carrying a status has answered the question itself."""
    from eval_harness import verify as v

    class _Resp:
        status_code = 404

    class _HTTPError(RuntimeError):
        response = _Resp()

    # The message mentions 503, but the carried status is authoritative.
    assert not v._is_retryable(_HTTPError("gateway said 503 earlier"))


def test_retry_records_the_attempt_count_it_actually_reached(monkeypatch) -> None:
    """A transient first attempt followed by a real error counts as two calls.

    Deriving the count from the final exception would report 1 and hide the
    fact that the host was already misbehaving.
    """
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, calls = _stub_fn(
        [_Boom("503 Service Unavailable"), _Boom("Request failed with status code 404")]
    )
    with pytest.raises(_Boom) as excinfo:
        v._call_with_retry(fn, object(), {})
    assert len(calls) == 2
    assert getattr(excinfo.value, "_eval_attempts", None) == 2


def test_retry_records_full_exhaustion(monkeypatch) -> None:
    """All three attempts spent is reported as three, not one."""
    from eval_harness import verify as v

    monkeypatch.setattr("time.sleep", lambda _s: None)
    fn, _calls = _stub_fn([_Boom("429 Too Many Requests")] * 3)
    with pytest.raises(_Boom) as excinfo:
        v._call_with_retry(fn, object(), {})
    assert getattr(excinfo.value, "_eval_attempts", None) == 3


def test_sync_path_call_is_time_bounded(monkeypatch) -> None:
    """The non-threadpool path must be bounded too, or a hang is unbounded."""
    import asyncio

    from eval_harness import verify as v

    monkeypatch.setattr(v, "_CALL_TIMEOUT", 0.05)

    async def hangs(_caller: object, **_kwargs: object) -> object:
        await asyncio.sleep(10)
        return {"never": True}

    with pytest.raises(TimeoutError):
        v._call_once(hangs, object(), {})
