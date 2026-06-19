"""
Smoke-test runner for generated time/ wrappers.
Transport: stdio  (uvx mcp-server-time)
Auth: none

Usage:
    python eval/time/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import time

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-time")

    # Skipped mutating tools: (none — all tools are read-only)

    # get_current_time -> CurrentTime
    current = await time.get_current_time(caller, timezone="America/New_York")
    print(f"get_current_time: datetime={current.get('datetime')!r}  day_of_week={current.get('day_of_week')!r}")

    # convert_time -> TimeConversion
    converted = await time.convert_time(
        caller,
        source_timezone="America/New_York",
        time="14:30",
        target_timezone="Europe/London",
    )
    print(f"convert_time: time_difference={converted.get('time_difference')!r}  target={converted.get('target')!r}")


if __name__ == "__main__":
    asyncio.run(main())
