"""
Smoke-test runner for generated semgrep/ wrappers.
Transport: Streamable HTTP  (https://mcp.semgrep.ai/mcp)
Auth: OAuth (browser flow via mcpgen)

Args come from eval/semgrep/semgrep.verify.json (real, pre-scrub probe args).

Usage:
    # First time: authenticate
    mcpgen login semgrep

    # Then run:
    python eval/semgrep/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
        # Skipped mutating tools: none — every semgrep tool is read-only
        # (the scan tools analyze code passed in the request; they write nothing).
        # No discriminated tools in semgrep.shapes.json, so one call per tool.

        # semgrep_whoami -> SemgrepIdentity
        me = await semgrep.semgrep_whoami(caller)
        print(f"semgrep_whoami: id={me.get('id')!r}  login={me.get('login')!r}")

        # get_supported_languages -> Any  (probe was inconclusive; shape unknown)
        langs = await semgrep.get_supported_languages(caller)
        print(f"get_supported_languages: {type(langs).__name__}")

        # semgrep_rule_schema -> Any
        schema = await semgrep.semgrep_rule_schema(caller)
        print(f"semgrep_rule_schema: {type(schema).__name__}")

        # get_abstract_syntax_tree -> Any  (args from verify.json)
        ast_result = await semgrep.get_abstract_syntax_tree(
            caller,
            code="def f():\n    return 1\n",
            language="python",
        )
        print(f"get_abstract_syntax_tree: {type(ast_result).__name__}")

        # semgrep_scan_remote -> Any  (args from verify.json)
        scan = await semgrep.semgrep_scan_remote(
            caller,
            code_files=[
                {"filename": "a.py", "content": "import os\nos.system(input())\n"}
            ],
        )
        print(f"semgrep_scan_remote: {type(scan).__name__}")

        # semgrep_scan_with_custom_rule -> Any  (args from verify.json)
        custom = await semgrep.semgrep_scan_with_custom_rule(
            caller,
            code_files=[
                {"filename": "a.py", "content": "import os\nos.system(input())\n"}
            ],
            rule=(
                "rules:\n"
                "  - id: t\n"
                "    pattern: os.system(...)\n"
                "    message: m\n"
                "    languages: [python]\n"
                "    severity: WARNING\n"
            ),
        )
        print(f"semgrep_scan_with_custom_rule: {type(custom).__name__}")

        # semgrep_findings -> Any  (args from verify.json)
        findings = await semgrep.semgrep_findings(
            caller,
            repos=["semgrep/semgrep"],
            limit=3,
        )
        print(f"semgrep_findings: {type(findings).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
