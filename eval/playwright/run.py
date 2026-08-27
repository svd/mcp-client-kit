"""
Smoke-test runner for generated playwright/ wrappers.
Transport: stdio  (npx -y @playwright/mcp@0.0.79 --headless --isolated --caps vision,pdf,devtools --init-page servers/playwright-init.ts --output-dir eval/playwright/.playwright-mcp)
Auth: none

Run from the repo root so the relative --init-page / --output-dir paths resolve:

    python eval/playwright/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import playwright

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
        # browser_navigate, browser_navigate_back, browser_press_key,
        # browser_resize, browser_resume, browser_run_code_unsafe,
        # browser_select_option, browser_type
        #
        # Also skipped (server declares readOnlyHint=true, but shapes.json marks
        # them _mutating_suspect — they inject overlays, write files, or drive a
        # recorder): browser_highlight, browser_hide_highlight, browser_pdf_save,
        # browser_take_screenshot, browser_start_tracing, browser_stop_tracing,
        # browser_start_video, browser_stop_video, browser_video_chapter,
        # browser_video_show_actions, browser_video_hide_actions
        #
        # Args come from eval/playwright/playwright.verify.json (real probe args),
        # except browser_tabs, which was not probed — action="list" is the
        # schema-minimal read-only variant and is synthetic.
        #
        # No browser_navigate call is needed: the server is launched with
        # --init-page servers/playwright-init.ts, so a page is already loaded.

        # browser_tabs -> Any  (synthetic arg: action="list" is the read-only action)
        tabs = await playwright.browser_tabs(caller, action="list")
        print(f"browser_tabs(list): {type(tabs).__name__} len={len(str(tabs))}")

        # browser_snapshot -> Any  (markdown + fenced YAML accessibility tree)
        snapshot = await playwright.browser_snapshot(caller)
        print(f"browser_snapshot: {type(snapshot).__name__} len={len(str(snapshot))}")

        # browser_find -> Any  (snapshot search snippets)
        found = await playwright.browser_find(caller, text="Playwright")
        print(f"browser_find(text='Playwright'): {type(found).__name__} len={len(str(found))}")

        # browser_console_messages -> Any  (probed variant: level='error')
        console_error = await playwright.browser_console_messages(caller, level="error")
        print(f"browser_console_messages(error): {type(console_error).__name__} len={len(str(console_error))}")

        # browser_console_messages -> Any  (probed variant: level='info')
        console_info = await playwright.browser_console_messages(caller, level="info")
        print(f"browser_console_messages(info): {type(console_info).__name__} len={len(str(console_info))}")

        # browser_console_messages -> Any  (probed variant: level='debug')
        console_debug = await playwright.browser_console_messages(caller, level="debug")
        print(f"browser_console_messages(debug): {type(console_debug).__name__} len={len(str(console_debug))}")
        # Variant level='warning' is declared by the schema but was never probed.

        # browser_network_requests -> Any  (probed variant: static=False)
        net_dynamic = await playwright.browser_network_requests(caller, static=False)
        print(f"browser_network_requests(static=False): {type(net_dynamic).__name__} len={len(str(net_dynamic))}")

        # browser_network_requests -> Any  (probed variant: static=True)
        net_static = await playwright.browser_network_requests(caller, static=True)
        print(f"browser_network_requests(static=True): {type(net_static).__name__} len={len(str(net_static))}")

        # browser_network_request -> Any  (human-readable request/response dump)
        net_one = await playwright.browser_network_request(caller, index=1)
        print(f"browser_network_request(index=1): {type(net_one).__name__} len={len(str(net_one))}")

        # browser_wait_for -> Any  (probe was inconclusive: no observable success payload)
        waited = await playwright.browser_wait_for(caller, time=1)
        print(f"browser_wait_for(time=1): {type(waited).__name__} len={len(str(waited))}")


if __name__ == "__main__":
    asyncio.run(main())
