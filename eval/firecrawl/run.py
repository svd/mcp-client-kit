"""
Smoke-test runner for generated firecrawl/ wrappers.
Transport: Streamable HTTP  (https://mcp.firecrawl.dev/v2/mcp)
Auth: Bearer token (set FIRECRAWL_API_KEY env var)

Usage:
    FIRECRAWL_API_KEY=<token> python eval/firecrawl/run.py
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
        # Skipped mutating tools: firecrawl_agent, firecrawl_crawl,
        # firecrawl_feedback, firecrawl_interact, firecrawl_interact_stop,
        # firecrawl_monitor_create, firecrawl_monitor_delete,
        # firecrawl_monitor_run, firecrawl_monitor_update,
        # firecrawl_search_feedback
        #
        # Skipped read-only tools whose probe was inconclusive (their only known
        # args are synthetic placeholder UUIDs that 404 against a live account):
        # firecrawl_agent_status, firecrawl_check_crawl_status,
        # firecrawl_monitor_check, firecrawl_monitor_checks, firecrawl_monitor_get
        # Supply a real job/monitor id to exercise them.

        # firecrawl_monitor_list -> MonitorList
        monitors = await firecrawl.firecrawl_monitor_list(caller, limit=5)
        print(
            f"firecrawl_monitor_list: success={monitors.get('success')!r} "
            f"data={len(monitors.get('data') or [])} monitor(s)"
        )

        # firecrawl_map -> list[MapLink]
        links = await firecrawl.firecrawl_map(caller, url="https://example.com", limit=5)
        print(f"firecrawl_map: {len(links)} link(s)")

        # firecrawl_search -> SearchResults  (discriminator=sources[0].type="web")
        s_web = await firecrawl.firecrawl_search(
            caller,
            query="model context protocol",
            limit=3,
            sources=[{"type": "web"}],
        )
        print(f"firecrawl_search(web): {len(s_web.get('web') or [])} result(s)")

        # firecrawl_search -> SearchResults  (discriminator=sources[0].type="news")
        s_news = await firecrawl.firecrawl_search(
            caller,
            query="model context protocol",
            limit=3,
            sources=[{"type": "news"}],
        )
        print(f"firecrawl_search(news): {len(s_news.get('news') or [])} result(s)")

        # firecrawl_search -> SearchResults  (discriminator=sources[0].type="images")
        s_images = await firecrawl.firecrawl_search(
            caller,
            query="model context protocol",
            limit=3,
            sources=[{"type": "images"}],
        )
        print(f"firecrawl_search(images): {len(s_images.get('images') or [])} result(s)")

        # firecrawl_scrape -> ScrapeResult  (discriminator=formats=["markdown"])
        sc_md = await firecrawl.firecrawl_scrape(
            caller,
            url="https://example.com",
            formats=["markdown"],
        )
        print(f"firecrawl_scrape(markdown): {len(sc_md.get('markdown') or '')} char(s)")

        # firecrawl_scrape -> ScrapeResult  (discriminator=formats=["html","links","summary"])
        sc_rich = await firecrawl.firecrawl_scrape(
            caller,
            url="https://example.com",
            formats=["html", "links", "summary"],
        )
        print(
            f"firecrawl_scrape(html+links+summary): "
            f"html={len(sc_rich.get('html') or '')} char(s)  "
            f"links={len(sc_rich.get('links') or [])}  "
            f"summary={(sc_rich.get('summary') or '')[:60]!r}"
        )

        # firecrawl_developer_search -> Any
        dev = await firecrawl.firecrawl_developer_search(
            caller,
            query="python asyncio TaskGroup cancellation",
            k=3,
        )
        print(f"firecrawl_developer_search: {type(dev).__name__}")

        # firecrawl_research_search_papers -> Any
        papers = await firecrawl.firecrawl_research_search_papers(
            caller,
            query="CRISPR gene editing safety",
            k=3,
        )
        print(f"firecrawl_research_search_papers: {type(papers).__name__}")

        # firecrawl_research_inspect_paper -> Any
        paper = await firecrawl.firecrawl_research_inspect_paper(
            caller,
            paperId="arxiv:2503.23278",
        )
        print(f"firecrawl_research_inspect_paper: {type(paper).__name__}")

        # firecrawl_research_read_paper -> Any
        read = await firecrawl.firecrawl_research_read_paper(
            caller,
            paperId="arxiv:2503.23278",
            question="What are the MCP lifecycle phases?",
            k=3,
        )
        print(f"firecrawl_research_read_paper: {type(read).__name__}")

        # firecrawl_research_related_papers -> Any
        related = await firecrawl.firecrawl_research_related_papers(
            caller,
            seed_ids=["arxiv:2503.23278"],
            intent="Find related work on MCP security",
            mode="similar",
        )
        print(f"firecrawl_research_related_papers: {type(related).__name__}")

        # firecrawl_research_search_github -> Any
        gh = await firecrawl.firecrawl_research_search_github(
            caller,
            query="model context protocol server",
            k=3,
        )
        print(f"firecrawl_research_search_github: {type(gh).__name__}")

        # firecrawl_parse -> ParseUploadTicket
        # verify.json holds a machine-specific fixture path; replace FIXTURE_PATH
        # with a local document of your own to exercise this tool.
        fixture_path = os.environ.get("FIRECRAWL_PARSE_FIXTURE")
        if fixture_path and os.path.exists(fixture_path):
            ticket = await firecrawl.firecrawl_parse(
                caller,
                filePath=fixture_path,
                formats=["markdown", "links", "summary"],
            )
            print(
                f"firecrawl_parse: success={ticket.get('success')!r} "
                f"mode={ticket.get('mode')!r}"
            )
        else:
            print("firecrawl_parse: skipped (set FIRECRAWL_PARSE_FIXTURE to a local file)")


if __name__ == "__main__":
    asyncio.run(main())
