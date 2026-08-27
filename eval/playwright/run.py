"""
Smoke-test runner for generated playwright/ wrappers.
Transport: stdio  (npx -y @playwright/mcp@0.0.79 --headless --isolated --caps vision,pdf,devtools --init-page servers/playwright-init.ts --output-dir eval/playwright/.playwright-mcp)
Auth: none

Run from the repository root — the launch command uses repo-relative paths
(--init-page servers/playwright-init.ts, --output-dir eval/playwright/.playwright-mcp):

Usage:
    python eval/playwright/run.py

Args come from eval/playwright/playwright.verify.json (real, pre-scrub probe args);
tools probed with no args use the empty-arg form. Every tool below returns `Any`
(the server answers in prose/text), so each print reports the payload type and a
short preview rather than drilling into fields.
"""
import asyncio
import os
import sys

# The wrapper module lives beside this file (eval/playwright/playwright.py), so the
# artifact dir itself goes on sys.path — inserted at 0 so it wins over any installed
# PyPI package that also answers to the name "playwright".
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import playwright

from mcpgen import McpBridgeCaller

LAUNCH = (
    "npx -y @playwright/mcp@0.0.79 --headless --isolated "
    "--caps vision,pdf,devtools "
    "--init-page servers/playwright-init.ts "
    "--output-dir eval/playwright/.playwright-mcp"
)


def preview(value: object, limit: int = 90) -> str:
    """One-line, length-capped rendering of an untyped (`Any`) response."""
    text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


async def main() -> None:
    caller = McpBridgeCaller(cmd=LAUNCH)

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating / page-driving tools: browser_annotate, browser_click,
        # browser_close, browser_drag, browser_drop, browser_evaluate,
        # browser_file_upload, browser_fill_form, browser_handle_dialog, browser_hover,
        # browser_mouse_click_xy, browser_mouse_down, browser_mouse_drag_xy,
        # browser_mouse_move_xy, browser_mouse_up, browser_mouse_wheel, browser_navigate,
        # browser_navigate_back, browser_press_key, browser_resize, browser_resume,
        # browser_run_code_unsafe, browser_select_option, browser_tabs, browser_type

        # --- Page state ------------------------------------------------------
        # browser_snapshot -> Any  (probed with no args)
        snapshot = await playwright.browser_snapshot(caller)
        print(f"browser_snapshot: {type(snapshot).__name__} {preview(snapshot)}")

        # browser_find -> Any
        found = await playwright.browser_find(caller, text="Playwright")
        print(f"browser_find: {type(found).__name__} {preview(found)}")

        # --- Console diagnostics (one call per probed arg variant) ------------
        # browser_console_messages -> Any  (level="error")
        console_errors = await playwright.browser_console_messages(caller, level="error")
        print(f"browser_console_messages(error): {type(console_errors).__name__} {preview(console_errors)}")

        # browser_console_messages -> Any  (level="info", all=True)
        console_info = await playwright.browser_console_messages(caller, level="info", all=True)
        print(f"browser_console_messages(info,all): {type(console_info).__name__} {preview(console_info)}")

        # --- Network diagnostics (one call per probed arg variant) ------------
        # browser_network_requests -> Any  (static=False)
        net_dynamic = await playwright.browser_network_requests(caller, static=False)
        print(f"browser_network_requests(static=False): {type(net_dynamic).__name__} {preview(net_dynamic)}")

        # browser_network_requests -> Any  (static=True)
        net_static = await playwright.browser_network_requests(caller, static=True)
        print(f"browser_network_requests(static=True): {type(net_static).__name__} {preview(net_static)}")

        # browser_network_request -> Any  (detail for one entry of the list above)
        net_one = await playwright.browser_network_request(caller, index=1)
        print(f"browser_network_request(1): {type(net_one).__name__} {preview(net_one)}")

        # --- Highlight overlay (paired: highlight, then hide) -----------------
        # browser_highlight -> Any
        highlighted = await playwright.browser_highlight(caller, element="page body", target="body")
        print(f"browser_highlight: {type(highlighted).__name__} {preview(highlighted)}")

        # browser_hide_highlight -> Any
        unhighlighted = await playwright.browser_hide_highlight(caller, element="page body", target="body")
        print(f"browser_hide_highlight: {type(unhighlighted).__name__} {preview(unhighlighted)}")

        # --- Capture ---------------------------------------------------------
        # browser_take_screenshot -> Any  (media tool: response carries image parts)
        shot = await playwright.browser_take_screenshot(caller, scale="css")
        print(f"browser_take_screenshot: {type(shot).__name__} {preview(shot)}")

        # browser_pdf_save -> Any  (probed with no args; writes into --output-dir)
        pdf = await playwright.browser_pdf_save(caller)
        print(f"browser_pdf_save: {type(pdf).__name__} {preview(pdf)}")

        # --- Timing ----------------------------------------------------------
        # browser_wait_for -> Any
        # NOTE: probe was inconclusive for this tool (no observable success payload),
        # so the shape is unknown; args below are the real probed args.
        waited = await playwright.browser_wait_for(caller, time=1)
        print(f"browser_wait_for: {type(waited).__name__} {preview(waited)}")

        # --- Tracing session (start must precede stop) ------------------------
        # browser_start_tracing -> Any  (probed with no args)
        trace_start = await playwright.browser_start_tracing(caller)
        print(f"browser_start_tracing: {type(trace_start).__name__} {preview(trace_start)}")

        # browser_stop_tracing -> Any  (probe inconclusive — shape unknown)
        trace_stop = await playwright.browser_stop_tracing(caller)
        print(f"browser_stop_tracing: {type(trace_stop).__name__} {preview(trace_stop)}")

        # --- Video session (start → annotate → stop) --------------------------
        # browser_start_video -> Any  (probed with no args)
        video_start = await playwright.browser_start_video(caller)
        print(f"browser_start_video: {type(video_start).__name__} {preview(video_start)}")

        # browser_video_show_actions -> Any  (probe inconclusive — shape unknown)
        show_actions = await playwright.browser_video_show_actions(caller)
        print(f"browser_video_show_actions: {type(show_actions).__name__} {preview(show_actions)}")

        # browser_video_chapter -> Any  (probe inconclusive — shape unknown)
        chapter = await playwright.browser_video_chapter(caller, title="Eval chapter", description="probe")
        print(f"browser_video_chapter: {type(chapter).__name__} {preview(chapter)}")

        # browser_video_hide_actions -> Any  (probe inconclusive — shape unknown)
        hide_actions = await playwright.browser_video_hide_actions(caller)
        print(f"browser_video_hide_actions: {type(hide_actions).__name__} {preview(hide_actions)}")

        # browser_stop_video -> Any  (probed with no args)
        video_stop = await playwright.browser_stop_video(caller)
        print(f"browser_stop_video: {type(video_stop).__name__} {preview(video_stop)}")


if __name__ == "__main__":
    asyncio.run(main())
