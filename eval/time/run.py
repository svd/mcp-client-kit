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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "time.py", which collides with the stdlib `time`
# module (already in sys.modules by the time this runs), so a plain `import
# time` would silently pick up the stdlib one. Load it by path as
# `time_wrappers` instead.
_spec = importlib.util.spec_from_file_location(
    "time_wrappers",
    os.path.join(os.path.dirname(__file__), "time.py"),
)
time_wrappers = importlib.util.module_from_spec(_spec)
sys.modules["time_wrappers"] = time_wrappers
_spec.loader.exec_module(time_wrappers)

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-time")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — this server is read-only.
        # Args below are the real probed args from time.verify.json.

        # get_current_time -> CurrentTime  (probed variant: UTC)
        now_utc = await time_wrappers.get_current_time(caller, timezone="UTC")
        print(
            f"get_current_time(UTC): timezone={now_utc.get('timezone')!r} "
            f"datetime={now_utc.get('datetime')!r} "
            f"day_of_week={now_utc.get('day_of_week')!r} "
            f"is_dst={now_utc.get('is_dst')!r}"
        )

        # get_current_time -> CurrentTime  (probed variant: America/New_York)
        now_ny = await time_wrappers.get_current_time(caller, timezone="America/New_York")
        print(
            f"get_current_time(America/New_York): timezone={now_ny.get('timezone')!r} "
            f"datetime={now_ny.get('datetime')!r} "
            f"day_of_week={now_ny.get('day_of_week')!r} "
            f"is_dst={now_ny.get('is_dst')!r}"
        )

        # convert_time -> TimeConversion
        conversion = await time_wrappers.convert_time(
            caller,
            source_timezone="UTC",
            time="14:30",
            target_timezone="Asia/Tokyo",
        )
        print(
            f"convert_time(UTC 14:30 -> Asia/Tokyo): "
            f"source={conversion.get('source')!r} "
            f"target={conversion.get('target')!r} "
            f"time_difference={conversion.get('time_difference')!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
