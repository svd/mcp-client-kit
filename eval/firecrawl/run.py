"""
Smoke-test runner for generated firecrawl/ wrappers.
Transport: Streamable HTTP  (https://mcp.firecrawl.dev/v2/mcp)
Auth: Bearer token (set FIRECRAWL_API_KEY env var)

Usage:
    FIRECRAWL_API_KEY=<token> python firecrawl/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import firecrawl

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.firecrawl.dev/v2/mcp"


async def main() -> None:
    bearer = os.environ.get("FIRECRAWL_API_KEY")
    if not bearer:
        sys.exit("FIRECRAWL_API_KEY not set")

    caller = McpBridgeCaller(url=SERVER_URL, bearer=bearer)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: firecrawl_agent, firecrawl_crawl, firecrawl_feedback,
        # firecrawl_interact, firecrawl_interact_stop, firecrawl_monitor_create,
        # firecrawl_monitor_delete, firecrawl_monitor_run, firecrawl_monitor_update,
        # firecrawl_search_feedback.
        # Also skipped (read-only but need a live id no probe produced):
        # firecrawl_agent_status, firecrawl_check_crawl_status, firecrawl_monitor_check,
        # firecrawl_monitor_checks, firecrawl_monitor_get.
        #
        # All args below come from firecrawl.verify.json (real, pre-scrub probe args).

        # --- discovery ------------------------------------------------------
        # firecrawl_map -> list[MapLink]
        links = await firecrawl.firecrawl_map(caller, url="https://example.com")
        print(f"firecrawl_map: {len(links)} link(s)"
              + (f"  first={links[0].get('url')!r}" if links else ""))

        # firecrawl_search -> SearchResult
        search = await firecrawl.firecrawl_search(
            caller, query="model context protocol", limit=3
        )
        print(
            "firecrawl_search: "
            f"success={search.get('success')!r}  id={search.get('id')!r}  "
            f"creditsUsed={search.get('creditsUsed')!r}"
        )

        # --- page content ---------------------------------------------------
        # firecrawl_scrape -> ScrapeResult
        # Not a discriminated tool (pass-2 probes on `url`/`proxy` were inconclusive),
        # but both probed URLs are exercised to cover the two recorded probe args.
        scrape_a = await firecrawl.firecrawl_scrape(caller, url="https://example.com")
        print(f"firecrawl_scrape(example.com): markdown={len(scrape_a.get('markdown') or '')} char(s)")

        scrape_b = await firecrawl.firecrawl_scrape(
            caller, url="https://en.wikipedia.org/wiki/Web_scraping"
        )
        print(f"firecrawl_scrape(wikipedia): markdown={len(scrape_b.get('markdown') or '')} char(s)")

        # firecrawl_parse -> ParseResult
        # `filePath` is a local path from verify.json; it is machine-specific and may
        # no longer exist. A local path returns the presigned-upload handshake
        # (mode discriminates), not parsed content.
        parse = await firecrawl.firecrawl_parse(
            caller,
            filePath="/private/tmp/claude-501/-Users-Sviataslau-Svirydau-src-mcp-client-kit-eval/a2739d5a-297c-4aad-a794-8e3888a40b91/scratchpad/sample.html",
        )
        print(
            "firecrawl_parse: "
            f"success={parse.get('success')!r}  mode={parse.get('mode')!r}  "
            f"message={parse.get('message')!r}"
        )

        # --- research (all return Markdown prose, typed as str/Any) ----------
        papers = await firecrawl.firecrawl_research_search_papers(
            caller, query="attention is all you need transformer", k=3
        )
        print(f"firecrawl_research_search_papers: {type(papers).__name__}")

        inspected = await firecrawl.firecrawl_research_inspect_paper(
            caller, paperId="arxiv:1706.03762"
        )
        print(f"firecrawl_research_inspect_paper: {type(inspected).__name__}")

        passages = await firecrawl.firecrawl_research_read_paper(
            caller, paperId="arxiv:1706.03762", question="What is multi-head attention?", k=3
        )
        print(f"firecrawl_research_read_paper: {type(passages).__name__}")

        related = await firecrawl.firecrawl_research_related_papers(
            caller,
            seed_ids=["arxiv:1706.03762"],
            intent="Find follow-up work on efficient attention",
            k=3,
        )
        print(f"firecrawl_research_related_papers: {type(related).__name__}")

        repos = await firecrawl.firecrawl_research_search_github(
            caller, query="model context protocol server", k=3
        )
        print(f"firecrawl_research_search_github: {type(repos).__name__}")

        dev = await firecrawl.firecrawl_developer_search(
            caller, query="python asyncio TaskGroup cancellation", k=3
        )
        print(f"firecrawl_developer_search: {type(dev).__name__}")

        # --- account state --------------------------------------------------
        # firecrawl_monitor_list -> MonitorListResult
        # `data` is [] on an account with no monitors; the element type is unmodelled.
        monitors = await firecrawl.firecrawl_monitor_list(caller, limit=5)
        print(
            "firecrawl_monitor_list: "
            f"success={monitors.get('success')!r}  "
            f"{len(monitors.get('data') or [])} monitor(s)"
        )


if __name__ == "__main__":
    asyncio.run(main())
