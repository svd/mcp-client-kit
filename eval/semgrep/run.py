"""
Smoke-test runner for generated semgrep/ wrappers.
Transport: Streamable HTTP  (https://mcp.semgrep.ai/mcp)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login semgrep

    # Then run:
    python eval/semgrep/run.py

Args come from semgrep.verify.json (real, pre-scrub probe args).

Note: `semgrep` is also the name of a PyPI package, so a plain `import semgrep`
could resolve to the installed distribution instead of the generated wrapper
module next to this file. The module is therefore loaded directly from its path
under the name `semgrep_wrappers`.
"""
import asyncio
import importlib.util
import os
import sys

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semgrep.py")
_spec = importlib.util.spec_from_file_location("semgrep_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
semgrep = importlib.util.module_from_spec(_spec)
sys.modules["semgrep_wrappers"] = semgrep
_spec.loader.exec_module(semgrep)

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.semgrep.ai/mcp"
SERVER_NAME = "semgrep"


def _summarize(label: str, result: object) -> None:
    """`semgrep_findings -> list[SemgrepFinding]` after the `findings` unwrap.

    With no matching findings the server answers with the plain string
    'No findings found' instead of an empty envelope, so the wrapper's
    list[SemgrepFinding] annotation holds for the populated case only.
    """
    if isinstance(result, list):
        print(f"{label}: {len(result)} finding(s)")
        for finding in result[:3]:
            print(
                f"    id={finding.get('id')!r}"
                f"  rule={finding.get('rule_path')!r}"
                f"  severity={finding.get('severity')!r}"
                f"  file={finding.get('file_path')!r}"
            )
    else:
        print(f"{label}: {type(result).__name__} -> {str(result)[:120]!r}")


async def main() -> None:
    # Ensure a valid OAuth token is available (silent refresh or browser prompt).
    # LoginWontHelp covers both failures the browser cannot fix: the token was
    # issued but the check after it failed, or the token endpoint was unreachable
    # so the cached grant could not be renewed. Stop rather than sending the user
    # back to the browser.
    try:
        await ensure_login(SERVER_NAME)
    except LoginWontHelp as exc:
        print(f"[{SERVER_NAME}] {exc}", file=sys.stderr)
        sys.exit(1)
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: one initialize() and one OAuth
    # pre-flight refresh, instead of one per tool call.
    async with caller.connected():
        # Skipped mutating tools: none — every tool this server exposes is
        # read-only (analysis and lookup); nothing creates or changes state.

        # semgrep_whoami -> SemgrepIdentity
        me = await semgrep.semgrep_whoami(caller)
        print(
            f"semgrep_whoami: id={me.get('id')!r}"
            f"  login={me.get('login')!r}"
            f"  name={me.get('name')!r}"
        )

        # get_supported_languages -> Any
        # Probe was inconclusive: the hosted server answered with a fixed
        # deprecation notice instead of a payload, so the shape is unknown.
        languages = await semgrep.get_supported_languages(caller)
        print(f"get_supported_languages: {type(languages).__name__} -> {str(languages)[:120]!r}")

        # semgrep_rule_schema -> Any
        # Genuinely text-returning: a ~37 KB YAML document, not JSON.
        schema = await semgrep.semgrep_rule_schema(caller)
        print(f"semgrep_rule_schema: {type(schema).__name__}  {len(str(schema))} char(s)")

        # get_abstract_syntax_tree -> Any   (args from semgrep.verify.json)
        # Probe was inconclusive: fixed deprecation notice, shape never observed.
        ast_result = await semgrep.get_abstract_syntax_tree(
            caller,
            code="def f(x):\n    return x + 1\n",
            language="python",
        )
        print(f"get_abstract_syntax_tree: {type(ast_result).__name__} -> {str(ast_result)[:120]!r}")

        # semgrep_findings -> list[SemgrepFinding]  (issue_type=ISSUE_TYPE_SAST)
        # Confirmed polymorphic on issue_type; SAST records carry
        # sastAttributes/aiTags/ruleset/policySlug/subcategories.
        # Args from semgrep.verify.json.
        sast = await semgrep.semgrep_findings(
            caller,
            issue_type="ISSUE_TYPE_SAST",
            repos=["svd/mcp-client-kit"],
            limit=3,
        )
        _summarize("semgrep_findings(ISSUE_TYPE_SAST)", sast)

        # semgrep_findings -> list[SemgrepFinding]  (issue_type=ISSUE_TYPE_SCA)
        # Second observed variant: SCA records carry
        # scaAttributes/vulnGroupKey/relatedIssues/note/activityHistory.
        # verify.json holds only the SAST probe args, so the discriminator is
        # swapped over the same repo/limit selection.
        sca = await semgrep.semgrep_findings(
            caller,
            issue_type="ISSUE_TYPE_SCA",
            repos=["svd/mcp-client-kit"],
            limit=3,
        )
        _summarize("semgrep_findings(ISSUE_TYPE_SCA)", sca)

        # The third variant, ISSUE_TYPE_SECRETS, was never probed — the probe
        # repo has no secrets findings and the server answers with the prose
        # sentinel 'No findings found'. Left out rather than asserted blind.

        # semgrep_scan_remote -> Any   (args from semgrep.verify.json)
        # Probe was inconclusive: fixed deprecation notice, shape never observed.
        scan = await semgrep.semgrep_scan_remote(
            caller,
            code_files=[{"filename": "a.py", "content": "import os\nos.system(input())\n"}],
        )
        print(f"semgrep_scan_remote: {type(scan).__name__} -> {str(scan)[:120]!r}")

        # semgrep_scan_with_custom_rule -> Any   (args from semgrep.verify.json)
        # Probe was inconclusive: fixed deprecation notice, shape never observed.
        custom = await semgrep.semgrep_scan_with_custom_rule(
            caller,
            code_files=[{"filename": "a.py", "content": "import os\nos.system(input())\n"}],
            rule=(
                "rules:\n"
                "  - id: test\n"
                "    pattern: os.system(...)\n"
                "    message: os.system call\n"
                "    languages: [python]\n"
                "    severity: WARNING\n"
            ),
        )
        print(f"semgrep_scan_with_custom_rule: {type(custom).__name__} -> {str(custom)[:120]!r}")


if __name__ == "__main__":
    asyncio.run(main())
