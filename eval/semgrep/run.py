"""
Smoke-test runner for generated semgrep/ wrappers.
Transport: Streamable HTTP  (https://mcp.semgrep.ai/mcp)
Auth: OAuth (browser flow via mcpgen)

Usage:
    # First time: authenticate
    mcpgen login semgrep

    # Then run:
    python semgrep/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import semgrep

from mcpgen import LoginWontHelp, McpBridgeCaller, ensure_login

SERVER_URL = "https://mcp.semgrep.ai/mcp"
SERVER_NAME = "semgrep"


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
        # Skipped mutating tools: none — all seven semgrep tools are read-only.
        # Args are the real pre-scrub probe args from semgrep.verify.json.
        # Four tools (get_abstract_syntax_tree, get_supported_languages,
        # semgrep_scan_remote, semgrep_scan_with_custom_rule) are deprecated on
        # the hosted server and return the same 473-byte plain-text notice, so
        # their prints show a text preview instead of typed field access.

        # semgrep_whoami -> SemgrepIdentity
        me = await semgrep.semgrep_whoami(caller)
        print(f"semgrep_whoami: id={me.get('id')!r}  login={me.get('login')!r}")

        # get_supported_languages -> Any  (deprecated: plain-text notice)
        langs = await semgrep.get_supported_languages(caller)
        print(f"get_supported_languages: {type(langs).__name__} {str(langs)[:80]!r}")

        # semgrep_rule_schema -> Any  (~35 KB YAML/JSON-schema document as a string)
        schema = await semgrep.semgrep_rule_schema(caller)
        print(f"semgrep_rule_schema: {type(schema).__name__} len={len(str(schema))}")

        # get_abstract_syntax_tree -> Any  (deprecated: plain-text notice)
        ast_out = await semgrep.get_abstract_syntax_tree(
            caller,
            code="def f(x):\n    return x + 1\n",
            language="python",
        )
        print(f"get_abstract_syntax_tree: {type(ast_out).__name__} {str(ast_out)[:80]!r}")

        # semgrep_findings -> list[SastFinding]  (discriminator issue_type=ISSUE_TYPE_SAST)
        sast = await semgrep.semgrep_findings(
            caller,
            issue_type="ISSUE_TYPE_SAST",
            repos=["svd/mcp-client-kit"],
            limit=5,
        )
        print(f"semgrep_findings(ISSUE_TYPE_SAST): {len(sast)} finding(s)")

        # semgrep_findings -> list[ScaFinding]  (discriminator issue_type=ISSUE_TYPE_SCA)
        # verify.json probed only the SAST variant; the SCA call reuses the same
        # repos/limit, which the SCA variant accepts unchanged.
        # ISSUE_TYPE_SECRETS is deliberately not called: it was probed and returned
        # prose ("No findings found"), so no variant shape was ever established.
        sca = await semgrep.semgrep_findings(
            caller,
            issue_type="ISSUE_TYPE_SCA",
            repos=["svd/mcp-client-kit"],
            limit=5,
        )
        print(f"semgrep_findings(ISSUE_TYPE_SCA): {len(sca)} finding(s)")

        # semgrep_scan_remote -> Any  (deprecated: plain-text notice)
        scan = await semgrep.semgrep_scan_remote(
            caller,
            code_files=[
                {"filename": "app.py", "content": "import os\nos.system(input())\n"}
            ],
        )
        print(f"semgrep_scan_remote: {type(scan).__name__} {str(scan)[:80]!r}")

        # semgrep_scan_with_custom_rule -> Any  (deprecated: plain-text notice)
        custom = await semgrep.semgrep_scan_with_custom_rule(
            caller,
            code_files=[
                {"filename": "app.py", "content": "import os\nos.system(input())\n"}
            ],
            rule=(
                "rules:\n"
                "  - id: os-system\n"
                "    pattern: os.system(...)\n"
                "    message: os.system call\n"
                "    languages: [python]\n"
                "    severity: WARNING\n"
            ),
        )
        print(f"semgrep_scan_with_custom_rule: {type(custom).__name__} {str(custom)[:80]!r}")


if __name__ == "__main__":
    asyncio.run(main())
