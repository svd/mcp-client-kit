"""
Smoke-test runner for generated everything/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-everything)
Auth: none

Usage:
    python everything/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import everything

from mcp_client_kit import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-everything")

    # echo → Any
    result = await everything.echo(caller, message='<example-message>')
    print(f"echo: {type(result).__name__}")

    # get-structured-content → WeatherData
    result = await everything.get-structured-content(caller, location='New York')
    print(f"get-structured-content: {result}")


if __name__ == "__main__":
    asyncio.run(main())
