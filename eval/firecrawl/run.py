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
        # Skipped mutating tools: firecrawl_feedback, firecrawl_search_feedback,
        # firecrawl_monitor_create, firecrawl_monitor_update, firecrawl_monitor_delete,
        # firecrawl_monitor_run, firecrawl_interact, firecrawl_interact_stop.
        # Also skipped (require a server-side ephemeral id we do not hold):
        # firecrawl_agent_status, firecrawl_check_crawl_status, firecrawl_monitor_get,
        # firecrawl_monitor_check, firecrawl_monitor_checks.
        # Also skipped (long-running/credit-heavy async jobs): firecrawl_agent, firecrawl_crawl.
        # All args below come from firecrawl.verify.json / shapes.json probed_args (real values).

        # firecrawl_monitor_list -> MonitorListResult
        monitors = await firecrawl.firecrawl_monitor_list(caller)
        print(
            f"firecrawl_monitor_list: success={monitors.get('success')!r}  "
            f"monitors={len(monitors.get('data') or [])}"
        )

        # firecrawl_map -> list[MapLink]  (vendor envelope unwrapped from `links`)
        links = await firecrawl.firecrawl_map(
            caller, url="https://modelcontextprotocol.io", limit=10
        )
        print(f"firecrawl_map: {len(links)} link(s)")

        # firecrawl_scrape -> ScrapeDocument  (discriminator=formats, variant: server default)
        scrape_default = await firecrawl.firecrawl_scrape(caller, url="https://example.com")
        print(
            f"firecrawl_scrape(default formats): keys={sorted(scrape_default)}  "
            f"markdown={len(scrape_default.get('markdown') or '')} char(s)"
        )

        # firecrawl_scrape -> ScrapeDocument  (discriminator=formats, variant: markdown+html+links+summary)
        scrape_all = await firecrawl.firecrawl_scrape(
            caller,
            url="https://example.com",
            formats=["markdown", "html", "links", "summary"],
        )
        print(
            f"firecrawl_scrape(4 formats): keys={sorted(scrape_all)}  "
            f"html={len(scrape_all.get('html') or '')} char(s)  "
            f"summary={len(scrape_all.get('summary') or '')} char(s)"
        )

        # firecrawl_search -> SearchResults  (discriminator=sources/categories, variant: default -> data.web)
        search_web = await firecrawl.firecrawl_search(
            caller, query="model context protocol specification", limit=3
        )
        print(f"firecrawl_search(default): web={len(search_web.get('web') or [])} result(s)")

        # firecrawl_search -> SearchResults  (variant: categories=[developer] -> data.web + `category`)
        search_dev = await firecrawl.firecrawl_search(
            caller,
            query="asyncio TaskGroup cancellation",
            limit=3,
            categories=["developer"],
        )
        print(f"firecrawl_search(developer): web={len(search_dev.get('web') or [])} result(s)")

        # firecrawl_search -> SearchResults  (variant: sources=[news] -> data.news)
        search_news = await firecrawl.firecrawl_search(
            caller, query="openai news", limit=3, sources=[{"type": "news"}]
        )
        print(f"firecrawl_search(news): news={len(search_news.get('news') or [])} result(s)")

        # firecrawl_search -> SearchResults  (variant: sources=[images] -> data.images)
        search_images = await firecrawl.firecrawl_search(
            caller, query="golden retriever", limit=3, sources=[{"type": "images"}]
        )
        print(f"firecrawl_search(images): images={len(search_images.get('images') or [])} result(s)")

        # firecrawl_developer_search -> Any  (markdown prose, not JSON)
        dev_search = await firecrawl.firecrawl_developer_search(
            caller, query="python asyncio TaskGroup cancellation semantics"
        )
        print(f"firecrawl_developer_search: {type(dev_search).__name__}")

        # firecrawl_research_search_papers -> Any  (markdown prose)
        papers = await firecrawl.firecrawl_research_search_papers(
            caller, query="CRISPR base editing off-target effects"
        )
        print(f"firecrawl_research_search_papers: {type(papers).__name__}")

        # firecrawl_research_inspect_paper -> Any  (markdown prose)
        paper = await firecrawl.firecrawl_research_inspect_paper(caller, paperId="pmid:34515826")
        print(f"firecrawl_research_inspect_paper: {type(paper).__name__}")

        # firecrawl_research_read_paper -> Any  (markdown prose; may be the
        # '(no full-text passages available for this paper)' sentinel — a valid result)
        passages = await firecrawl.firecrawl_research_read_paper(
            caller,
            paperId="pmid:34515826",
            question="What reduces off-target effects of base editors?",
        )
        print(f"firecrawl_research_read_paper: {type(passages).__name__}")

        # firecrawl_research_related_papers -> Any  (markdown prose)
        related = await firecrawl.firecrawl_research_related_papers(
            caller,
            seed_ids=["pmid:34515826"],
            intent="Find papers on reducing base editor off-target activity",
        )
        print(f"firecrawl_research_related_papers: {type(related).__name__}")

        # firecrawl_research_search_github -> Any  (markdown prose)
        gh = await firecrawl.firecrawl_research_search_github(caller, query="httpx connection pool leak")
        print(f"firecrawl_research_search_github: {type(gh).__name__}")

        # firecrawl_parse -> ParseResult  (phase one: `filePath` returns signed upload
        # instructions; phase two via `uploadRef` needs a raw HTTP upload and is not exercised.
        # The probed path /tmp/sample.pdf is machine-local — substitute your own file.)
        parsed = await firecrawl.firecrawl_parse(
            caller, filePath="/tmp/sample.pdf", formats=["markdown"]
        )
        print(f"firecrawl_parse: success={parsed.get('success')!r}")


if __name__ == "__main__":
    asyncio.run(main())
