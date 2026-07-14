"""
Smoke-test runner for generated time/ wrappers.
Transport: stdio  (uvx mcp-server-time)
Auth: none

Usage:
    python eval/time/run.py

Note: the generated wrapper module is named `time.py`, which collides with
the Python stdlib `time` module. CPython's import system always resolves a
bare `import time` to the builtin module regardless of sys.path, so a plain
`import time` would silently import the stdlib module instead of the local
wrapper (and every call below would raise AttributeError). We load the
wrapper module directly from its file path under a distinct name to avoid
the collision.
"""
import asyncio
import importlib.util
import os

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "time.py")
_spec = importlib.util.spec_from_file_location("time_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
time_wrappers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(time_wrappers)

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="uvx mcp-server-time")

    # Skipped mutating tools: (none — all tools are read-only)

    # get_current_time -> CurrentTime
    current = await time_wrappers.get_current_time(caller, timezone="America/New_York")
    print(f"get_current_time: datetime={current.get('datetime')!r}  day_of_week={current.get('day_of_week')!r}")

    # convert_time -> TimeConversion
    converted = await time_wrappers.convert_time(
        caller,
        source_timezone="America/New_York",
        time="14:30",
        target_timezone="Europe/London",
    )
    print(f"convert_time: time_difference={converted.get('time_difference')!r}  target={converted.get('target')!r}")


if __name__ == "__main__":
    asyncio.run(main())
