"""Contract tests between the run-eval workflow and the agent prompt template.

The analyze stage locates the generate agent's transcript by grepping for a
marker sentence that lives in the agent prompt template. Nothing at runtime
fails loudly when those two drift apart — the analyzer just silently picks a
different transcript and attributes harness actions to the skill under test,
which is the exact bug this contract exists to prevent.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".claude" / "workflows" / "run-eval.js"
TEMPLATE = ROOT / "agents" / "server-eval-agent.md"

MARKER_TEMPLATE = "skill for the **{{SERVER_NAME}}** MCP server"
MARKER_WORKFLOW = "skill for the **${server.name}** MCP server"

# The analyzer's own transcript necessarily contains the marker (the instruction embeds
# it), so it self-excludes on a string that appears only in the analyze prompt.
EXCLUSION = "Locating the transcript"


def test_marker_present_in_agent_template() -> None:
    """The generate prompt must still carry the sentence the analyzer greps for."""
    assert MARKER_TEMPLATE in TEMPLATE.read_text(encoding="utf-8")


def test_marker_present_in_analyze_prompt() -> None:
    """The analyze stage must still grep for that same sentence."""
    assert MARKER_WORKFLOW in WORKFLOW.read_text(encoding="utf-8")


def test_exclusion_string_absent_from_agent_template() -> None:
    """The exclusion string must not reach the generate agent's transcript.

    If it ever enters the generate prompt, the generate transcript is excluded
    too and the analyzer finds zero candidates — a silent wipe-out of the whole
    analyze stage.
    """
    assert EXCLUSION not in TEMPLATE.read_text(encoding="utf-8")


def test_exclusion_string_present_in_analyze_prompt() -> None:
    """The self-exclusion only works because the analyze prompt does contain it."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    marker_at = workflow.index(MARKER_WORKFLOW)
    assert EXCLUSION in workflow[marker_at - 2000 : marker_at + 2000]


def test_exclusion_string_is_not_session_analyzer() -> None:
    """Regression guard: 'session-analyzer' is unusable as the exclusion string.

    Every workflow subagent transcript carries it in the runtime skill roster —
    including the generate agent's — so filtering on it excludes every candidate.
    Verified empirically against a real run before this guard was written.
    """
    assert EXCLUSION != "session-analyzer"
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Do NOT filter on the string" in workflow


def test_transcript_path_is_scoped_to_this_project() -> None:
    """Searching ~/.claude at large can pick another project's newer workflow run."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "PROJECT_SLUG" in workflow
    assert "subagents/workflows" in workflow


def test_verdict_gap_codes_match_report_gap_codes() -> None:
    """verify.py's gap prefixes and report.py's gap rendering must not drift."""
    verify_src = (ROOT / "eval_harness" / "verify.py").read_text(encoding="utf-8")
    report_src = (ROOT / "eval_harness" / "report.py").read_text(encoding="utf-8")
    for code in ("shapes_json_empty", "probe_inconclusive"):
        assert code in verify_src, code
        assert code in report_src, f"{code} emitted but never rendered"


def test_analyze_failure_is_surfaced_not_swallowed() -> None:
    """A failed analyze stage must reach the run summary and the narrative stage."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "analyzed" in workflow
    assert "Analyze FAILED after retry" in workflow


def test_manifest_agent_schema_requires_seed() -> None:
    """`seed` must be required, else the agent may omit it and seeding is skipped."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    required = re.search(r"required:\s*\[([^\]]*'auth_notes'[^\]]*)\]", workflow)
    assert required is not None, "manifest-agent required list not found"
    assert "'seed'" in required.group(1)


def test_verify_stage_runs_before_analyze_stage() -> None:
    """Analyze reads result.json as ground truth, so verify must precede it.

    Anchored on the agent labels rather than comment text, so relabelled comments
    over reordered code cannot pass.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.index("label: `verify:${server.name}`") < workflow.index(
        "label: `analyze:${server.name}`"
    )


def test_run_nonce_is_threaded_into_generate_and_analyze() -> None:
    """The per-run id is what makes transcript lookup immune to concurrent runs.

    It must reach the generate agent's transcript (via the template) AND be
    quoted in the analyze prompt, or the lookup silently falls back to ambiguity.
    """
    assert "{{RUN_NONCE}}" in TEMPLATE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "RUN_NONCE" in workflow
    assert "run_nonce" in workflow, "the nonce must be minted by an agent, not the script"
    marker_at = workflow.index(MARKER_WORKFLOW)
    assert "${RUN_NONCE}" in workflow[marker_at - 2000 : marker_at + 2000]


def test_transcript_lookup_does_not_rank_by_mtime() -> None:
    """Recency cannot disambiguate two concurrent runs; the run id must do it."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Do not rank candidates by" in workflow


def test_every_template_placeholder_is_substituted() -> None:
    """An unsubstituted {{PLACEHOLDER}} reaches the agent as literal braces."""
    template = TEMPLATE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    # {{PLACEHOLDERS}} is the template's own prose describing the mechanism.
    found = set(re.findall(r"\{\{[A-Z_]+\}\}", template)) - {"{{PLACEHOLDERS}}"}
    assert found, "no placeholders found — did the template format change?"
    for placeholder in sorted(found):
        name = placeholder.strip("{}")
        assert f"{{\\{{{name}\\}}}}" in workflow or f"\\{{\\{{{name}\\}}\\}}" in workflow, (
            f"{placeholder} is in the template but never substituted by the workflow"
        )
