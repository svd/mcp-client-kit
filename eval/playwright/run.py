"""
Smoke-test runner for generated playwright/ wrappers.
Transport: stdio  (npx -y @playwright/mcp@0.0.79 --headless --isolated ...)
Auth: none

Usage:
    # run from the repo root - the launch command uses repo-relative paths
    python eval/playwright/run.py

Args come from playwright.verify.json (real, pre-scrub probe args); tools with no
probed args are called bare, exactly as they were probed.

Note: `playwright` is also the name of a PyPI package, so a plain `import playwright`
could resolve to the installed distribution instead of the generated wrapper module
next to this file. The module is therefore loaded directly from its path under the
name `playwright_wrappers`.
"""
import asyncio
import importlib.util
import os
import sys

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playwright.py")
_spec = importlib.util.spec_from_file_location("playwright_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
playwright = importlib.util.module_from_spec(_spec)
sys.modules["playwright_wrappers"] = playwright
_spec.loader.exec_module(playwright)

from mcpgen import McpBridgeCaller

LAUNCH = (
    "npx -y @playwright/mcp@0.0.79 --headless --isolated "
    "--caps vision,pdf,devtools "
    "--init-page servers/playwright-init.ts "
    "--output-dir eval/playwright/.playwright-mcp"
)


async def main() -> None:
    caller = McpBridgeCaller(cmd=LAUNCH)

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: browser_annotate, browser_click, browser_close,
        # browser_drag, browser_drop, browser_evaluate, browser_file_upload,
        # browser_fill_form, browser_handle_dialog, browser_hover,
        # browser_mouse_click_xy, browser_mouse_down, browser_mouse_drag_xy,
        # browser_mouse_move_xy, browser_mouse_up, browser_mouse_wheel,
        # browser_navigate, browser_navigate_back, browser_press_key, browser_resize,
        # browser_resume, browser_run_code_unsafe, browser_select_option,
        # browser_tabs, browser_type
        # (browser_tabs is read-only only for action="list"; its other actions open
        # and close tabs, so it is skipped wholesale.)
        #
        # No tool in playwright.shapes.json carries a discriminator, so each tool
        # gets exactly one call. Every shaped payload is markdown prose (str) rather
        # than JSON, so all wrappers return Any and the prints report type + size.

        # browser_snapshot -> Any  (accessibility tree as markdown/YAML prose)
        snapshot = await playwright.browser_snapshot(caller)
        print(f"browser_snapshot: {type(snapshot).__name__} len={len(str(snapshot))}")

        # browser_find -> Any  (markdown search snippets over the a11y tree)
        found = await playwright.browser_find(caller, text="Playwright")
        print(f"browser_find: {type(found).__name__} len={len(str(found))}")

        # browser_highlight -> Any  (ack prose)
        highlighted = await playwright.browser_highlight(
            caller, target="h1", element="page heading"
        )
        print(f"browser_highlight: {type(highlighted).__name__} len={len(str(highlighted))}")

        # browser_hide_highlight -> Any  (ack prose; probed with no args)
        unhighlighted = await playwright.browser_hide_highlight(caller)
        print(
            f"browser_hide_highlight: {type(unhighlighted).__name__} "
            f"len={len(str(unhighlighted))}"
        )

        # browser_console_messages -> Any  ('level' selects content, not shape)
        console = await playwright.browser_console_messages(caller, level="info")
        print(f"browser_console_messages: {type(console).__name__} len={len(str(console))}")

        # browser_network_requests -> Any  (numbered markdown list)
        requests = await playwright.browser_network_requests(caller, static=False)
        print(f"browser_network_requests: {type(requests).__name__} len={len(str(requests))}")

        # browser_network_request -> Any  (markdown detail block for one request)
        request = await playwright.browser_network_request(caller, index=1)
        print(f"browser_network_request: {type(request).__name__} len={len(str(request))}")

        # browser_take_screenshot -> Any  (2-block content list: text + image)
        shot = await playwright.browser_take_screenshot(caller, scale="css")
        print(f"browser_take_screenshot: {type(shot).__name__} len={len(str(shot))}")

        # browser_pdf_save -> Any  (ack naming the file under --output-dir)
        pdf = await playwright.browser_pdf_save(caller)
        print(f"browser_pdf_save: {type(pdf).__name__} len={len(str(pdf))}")

        # browser_wait_for -> Any  (probe was inconclusive: every probe process
        # answered 'Error: No open pages available.' - one live connection here may
        # do better, since --init-page opens a page for the session)
        waited = await playwright.browser_wait_for(caller, time=1)
        print(f"browser_wait_for: {type(waited).__name__} len={len(str(waited))}")

        # Tracing pair - browser_stop_tracing probed inconclusive because each probe
        # ran in a fresh process with no tracing session to stop. Started here first
        # so the stop call has a real session behind it.
        trace_start = await playwright.browser_start_tracing(caller)
        print(f"browser_start_tracing: {type(trace_start).__name__} len={len(str(trace_start))}")

        trace_stop = await playwright.browser_stop_tracing(caller)
        print(f"browser_stop_tracing: {type(trace_stop).__name__} len={len(str(trace_stop))}")

        # Video group - the three video_* tools below probed inconclusive for the same
        # reason (fresh process, no open page / no recording). Ordered start -> annotate
        # -> stop so each one runs against live state.
        video_start = await playwright.browser_start_video(caller)
        print(f"browser_start_video: {type(video_start).__name__} len={len(str(video_start))}")

        show_actions = await playwright.browser_video_show_actions(caller)
        print(
            f"browser_video_show_actions: {type(show_actions).__name__} "
            f"len={len(str(show_actions))}"
        )

        chapter = await playwright.browser_video_chapter(caller, title="Eval chapter")
        print(f"browser_video_chapter: {type(chapter).__name__} len={len(str(chapter))}")

        hide_actions = await playwright.browser_video_hide_actions(caller)
        print(
            f"browser_video_hide_actions: {type(hide_actions).__name__} "
            f"len={len(str(hide_actions))}"
        )

        video_stop = await playwright.browser_stop_video(caller)
        print(f"browser_stop_video: {type(video_stop).__name__} len={len(str(video_stop))}")


if __name__ == "__main__":
    asyncio.run(main())
