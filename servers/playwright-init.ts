// Init page for the playwright eval entry (referenced from servers.toml `launch`).
//
// Every `mcpgen probe` spawns a fresh process, and the Playwright MCP server keeps its
// browser inside that process, so state never survives a call. Without this the browser
// sits on about:blank for every probe: browser_snapshot returns an empty YAML block,
// browser_network_requests an empty result, browser_find "No matches found", and
// browser_wait_for fails outright with "No open pages available".
//
// `--init-page` runs against the Playwright page object as it is created, before any tool
// is dispatched, so it is the one place a non-mutating run can put the browser on a real
// page. Navigating here is not a probe and does not count as calling a mutating tool —
// browser_navigate stays skipped.
//
// playwright.dev is deliberate. It is maintained by the same team as the MCP server, so it
// is stable, and unlike a minimal page it exercises arguments that would otherwise go
// unprobed: dozens of static subresources give browser_network_requests something to say
// about `static` and `filter`, a deep DOM gives browser_snapshot a meaningful `depth`, and
// a tall page makes browser_take_screenshot's `fullPage` and element-scoped `ref` variants
// observable. The trade-off is a live network dependency and page content that drifts
// between runs; if that drift ever makes the idempotency check noisy, replace this with a
// committed local fixture served over file:// from the workspace root.
export default async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('https://playwright.dev/', { waitUntil: 'load' });
  // Give browser_console_messages something to report at every level its enum accepts.
  await page.evaluate(() => {
    console.debug('mcp-client-kit-eval: sample debug');
    console.log('mcp-client-kit-eval: init-page ready');
    console.warn('mcp-client-kit-eval: sample warning');
    console.error('mcp-client-kit-eval: sample error');
  });
};
