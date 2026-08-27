"""
Smoke-test runner for generated firecrawl/ wrappers.
Transport: Streamable HTTP  (https://mcp.firecrawl.dev/v2/mcp)
Auth: Bearer token (set FIRECRAWL_API_KEY env var)

Usage:
    FIRECRAWL_API_KEY=<token> python eval/firecrawl/run.py

Args come from firecrawl.verify.json (real, pre-scrub probe args).

Note: the wrapper module sits next to this file inside a directory that is not
an importable package, so a plain `import firecrawl` would resolve to the
namespace package rather than the module. We load it from its file path under
an explicit identifier instead.
"""
import asyncio
import importlib.util
import os
import sys

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firecrawl.py")
_spec = importlib.util.spec_from_file_location("firecrawl_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
firecrawl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(firecrawl)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.firecrawl.dev/v2/mcp"


def _preview(text: object, limit: int = 100) -> str:
    s = "" if text is None else str(text)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + "..."


async def main() -> None:
    bearer = os.environ.get("FIRECRAWL_API_KEY")
    if not bearer:
        sys.exit("FIRECRAWL_API_KEY not set")

    caller = McpBridgeCaller(url=SERVER_URL, bearer=bearer)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: firecrawl_monitor_create, firecrawl_monitor_update,
        # firecrawl_monitor_delete, firecrawl_monitor_run, firecrawl_feedback,
        # firecrawl_search_feedback, firecrawl_interact, firecrawl_interact_stop,
        # firecrawl_agent, firecrawl_crawl, firecrawl_parse.
        # Also skipped (read-only but require a live server-side id this runner
        # cannot obtain without first starting a job): firecrawl_agent_status,
        # firecrawl_check_crawl_status, firecrawl_monitor_get,
        # firecrawl_monitor_check, firecrawl_monitor_checks.

        # firecrawl_monitor_list -> MonitorListResponse
        monitors = await firecrawl.firecrawl_monitor_list(caller, limit=5)
        print(
            f"firecrawl_monitor_list: success={monitors.get('success')!r} "
            f"{len(monitors.get('data') or [])} monitor(s)"
        )

        # firecrawl_map -> list[MapLink]
        links = await firecrawl.firecrawl_map(
            caller, url="https://modelcontextprotocol.io", limit=10
        )
        print(f"firecrawl_map: {len(links)} link(s)")
        if links:
            print(f"  first: url={links[0].get('url')!r} title={links[0].get('title')!r}")

        # firecrawl_scrape -> ScrapeResult  (discriminator=formats=['markdown'])
        scrape_md = await firecrawl.firecrawl_scrape(
            caller, url="https://modelcontextprotocol.io", formats=["markdown"]
        )
        print(
            f"firecrawl_scrape(['markdown']): keys={sorted(scrape_md)} "
            f"markdown={_preview(scrape_md.get('markdown'))!r}"
        )

        # firecrawl_scrape -> ScrapeResult  (discriminator=formats=['links','summary'])
        scrape_ls = await firecrawl.firecrawl_scrape(
            caller, url="https://modelcontextprotocol.io", formats=["links", "summary"]
        )
        print(
            f"firecrawl_scrape(['links','summary']): keys={sorted(scrape_ls)} "
            f"{len(scrape_ls.get('links') or [])} link(s) "
            f"summary={_preview(scrape_ls.get('summary'))!r}"
        )

        # firecrawl_search -> SearchResponse  (discriminator=sources=[{'type':'web'}])
        # verify.json probed the 'news' source; the 'web' and 'images' variants come
        # from the shape-spec discriminator note, which records all three shapes.
        search_web = await firecrawl.firecrawl_search(
            caller, query="AI regulation", limit=3, sources=[{"type": "web"}]
        )
        print(
            f"firecrawl_search(web): success={search_web.get('success')!r} "
            f"id={search_web.get('id')!r} creditsUsed={search_web.get('creditsUsed')!r} "
            f"{len((search_web.get('data') or {}).get('web') or [])} result(s)"
        )

        # firecrawl_search -> SearchResponse  (discriminator=sources=[{'type':'news'}])
        search_news = await firecrawl.firecrawl_search(
            caller, query="AI regulation", limit=3, sources=[{"type": "news"}]
        )
        print(
            f"firecrawl_search(news): success={search_news.get('success')!r} "
            f"id={search_news.get('id')!r} creditsUsed={search_news.get('creditsUsed')!r} "
            f"{len((search_news.get('data') or {}).get('news') or [])} result(s)"
        )

        # firecrawl_search -> SearchResponse  (discriminator=sources=[{'type':'images'}])
        search_images = await firecrawl.firecrawl_search(
            caller, query="AI regulation", limit=3, sources=[{"type": "images"}]
        )
        print(
            f"firecrawl_search(images): success={search_images.get('success')!r} "
            f"id={search_images.get('id')!r} creditsUsed={search_images.get('creditsUsed')!r} "
            f"{len((search_images.get('data') or {}).get('images') or [])} result(s)"
        )

        # firecrawl_developer_search -> Any  (observed shape: markdown str)
        dev = await firecrawl.firecrawl_developer_search(
            caller, query="python asyncio TaskGroup cancellation", k=3
        )
        print(f"firecrawl_developer_search: {type(dev).__name__}  {_preview(dev)!r}")

        # firecrawl_research_search_papers -> Any  (observed shape: markdown str)
        papers = await firecrawl.firecrawl_research_search_papers(
            caller, query="transformer attention mechanism", k=3
        )
        print(f"firecrawl_research_search_papers: {type(papers).__name__}  {_preview(papers)!r}")

        # firecrawl_research_search_github -> Any  (observed shape: markdown str)
        gh = await firecrawl.firecrawl_research_search_github(
            caller, query="model context protocol server", k=3
        )
        print(f"firecrawl_research_search_github: {type(gh).__name__}  {_preview(gh)!r}")

        # firecrawl_research_inspect_paper -> Any  (observed shape: markdown str)
        paper = await firecrawl.firecrawl_research_inspect_paper(
            caller, paperId="arxiv:2604.00965"
        )
        print(f"firecrawl_research_inspect_paper: {type(paper).__name__}  {_preview(paper)!r}")

        # firecrawl_research_read_paper -> Any  (observed shape: markdown str)
        passages = await firecrawl.firecrawl_research_read_paper(
            caller,
            paperId="arxiv:2604.00965",
            question="How does multi-head attention work?",
            k=3,
        )
        print(f"firecrawl_research_read_paper: {type(passages).__name__}  {_preview(passages)!r}")

        # firecrawl_research_related_papers -> Any  (observed shape: markdown str)
        related = await firecrawl.firecrawl_research_related_papers(
            caller,
            seed_ids=["arxiv:1706.03762"],
            intent="papers building on transformer attention",
            mode="citers",
            k=3,
        )
        print(f"firecrawl_research_related_papers: {type(related).__name__}  {_preview(related)!r}")


if __name__ == "__main__":
    asyncio.run(main())
