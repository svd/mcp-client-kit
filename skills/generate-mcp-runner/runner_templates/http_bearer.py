"""
Smoke-test runner for generated $server_name/ wrappers.
Transport: HTTP  ($launch)
Auth: Bearer token (set ${bearer_env_var} env var)

Usage:
    ${bearer_env_var}=<token> python $server_name/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import $module_name

from mcpgen import McpBridgeCaller

SERVER_URL = "$launch"


async def main() -> None:
    bearer = os.environ.get("${bearer_env_var}")
    if not bearer:
        sys.exit("${bearer_env_var} not set")

    caller = McpBridgeCaller(url=SERVER_URL, bearer=bearer)

$demo_calls

if __name__ == "__main__":
    asyncio.run(main())
