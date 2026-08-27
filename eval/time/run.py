"""
Smoke-test runner for generated time/ wrappers.
Transport: stdio  (uvx mcp-server-time)
Auth: none

Usage:
    python eval/time/run.py
"""
import asyncio
import importlib.util
import os
import sys

# The wrapper module is named "time", which collides with the stdlib module
# already imported by asyncio. Load it by path instead of via sys.path so the
# generated wrappers are the ones exercised here.
_WRAPPER_PATH = os.path.join(os.path.dirname(__file__), "time.py")
_spec = importlib.util.spec_from_file_location("time_wrappers", _WRAPPER_PATH)
time_wrappers = importlib.util.module_from_spec(_spec)
sys.modules["time_wrappers"] = time_wrappers
_spec.loader.exec_module(time_wrappers)

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-time")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — all tools are read-only)
        # Args are the real probed args from time.verify.json.

        # get_current_time -> CurrentTime
        now = await time_wrappers.get_current_time(caller, timezone="America/New_York")
        print(
            f"get_current_time: timezone={now.get('timezone')!r}  "
            f"datetime={now.get('datetime')!r}  "
            f"day_of_week={now.get('day_of_week')!r}  is_dst={now.get('is_dst')!r}"
        )

        # convert_time -> ConvertedTime
        converted = await time_wrappers.convert_time(
            caller,
            source_timezone="America/New_York",
            time="14:30",
            target_timezone="Asia/Tokyo",
        )
        print(
            f"convert_time: source={converted.get('source')!r}  "
            f"target={converted.get('target')!r}  "
            f"time_difference={converted.get('time_difference')!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
