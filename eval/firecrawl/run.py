"""
Smoke-test runner for generated firecrawl/ wrappers.
Transport: Streamable HTTP  (https://mcp.firecrawl.dev/v2/mcp)
Auth: Bearer token (set FIRECRAWL_API_KEY env var)

Args come from firecrawl.verify.json (real, pre-scrub probe args).

Two deviations from the verify.json args, both deliberate:
  * firecrawl_map declares a discriminator (sitemap) with three variants
    (include / skip / only); only "only" was probed. The "include" and "skip"
    calls below reuse the probed url and limit, varying just the discriminator.
  * firecrawl_parse was probed with an absolute path to a machine-local
    scratchpad file. That path is not portable and must not be committed, so the
    file is taken from FIRECRAWL_PARSE_FILE and the call is skipped when unset.

Usage:
    FIRECRAWL_API_KEY=<token> python eval/firecrawl/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module lives at eval/firecrawl/firecrawl.py, inside a directory
# that is itself named "firecrawl". A plain `import firecrawl` with eval/ on
# sys.path would resolve to that directory as a namespace package instead of the
# module, so load the file by path as `firecrawl_wrappers`.
_spec = importlib.util.spec_from_file_location(
    "firecrawl_wrappers",
    os.path.join(os.path.dirname(__file__), "firecrawl.py"),
)
assert _spec is not None and _spec.loader is not None
firecrawl_wrappers = importlib.util.module_from_spec(_spec)
sys.modules["firecrawl_wrappers"] = firecrawl_wrappers
_spec.loader.exec_module(firecrawl_wrappers)

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
        # Skipped mutating tools: firecrawl_monitor_create,
        # firecrawl_monitor_update, firecrawl_monitor_delete,
        # firecrawl_monitor_run, firecrawl_feedback, firecrawl_search_feedback,
        # firecrawl_interact_stop.
        # Also skipped (job-launching or id-dependent, never probed):
        # firecrawl_agent, firecrawl_agent_status, firecrawl_crawl,
        # firecrawl_check_crawl_status, firecrawl_interact,
        # firecrawl_monitor_get, firecrawl_monitor_check,
        # firecrawl_monitor_checks.

        # firecrawl_search -> SearchResults  (unwrap: data)
        search = await firecrawl_wrappers.firecrawl_search(
            caller,
            query="golden retriever",
            limit=3,
            sources=[{"type": "images"}],
        )
        print(
            f"firecrawl_search: web={len(search.get('web') or [])} "
            f"news={len(search.get('news') or [])} "
            f"images={len(search.get('images') or [])}"
        )

        # firecrawl_scrape -> ScrapeResult
        scrape = await firecrawl_wrappers.firecrawl_scrape(
            caller,
            url="https://example.com",
            formats=["markdown", "links", "html", "summary"],
        )
        print(
            f"firecrawl_scrape: markdown={len(scrape.get('markdown') or '')}b "
            f"links={len(scrape.get('links') or [])} "
            f"title={(scrape.get('metadata') or {}).get('title')!r}"
        )

        # firecrawl_map -> list[MapLinkUrlOnly]  (discriminator sitemap="only")
        map_only = await firecrawl_wrappers.firecrawl_map(
            caller,
            url="https://docs.python.org",
            sitemap="only",
            limit=10,
        )
        print(f"firecrawl_map(only): {len(map_only)} link(s)")

        # firecrawl_map -> list[MapLink]  (discriminator sitemap="include")
        map_include = await firecrawl_wrappers.firecrawl_map(
            caller,
            url="https://docs.python.org",
            sitemap="include",
            limit=10,
        )
        print(f"firecrawl_map(include): {len(map_include)} link(s)")

        # firecrawl_map -> list[MapLink]  (discriminator sitemap="skip")
        map_skip = await firecrawl_wrappers.firecrawl_map(
            caller,
            url="https://docs.python.org",
            sitemap="skip",
            limit=10,
        )
        print(f"firecrawl_map(skip): {len(map_skip)} link(s)")

        # firecrawl_developer_search -> Any  (observed shape: str)
        dev = await firecrawl_wrappers.firecrawl_developer_search(
            caller,
            query="python asyncio TaskGroup cancellation semantics",
            k=3,
        )
        print(f"firecrawl_developer_search: {type(dev).__name__} len={len(str(dev))}")

        # firecrawl_research_search_papers -> Any  (observed shape: str)
        papers = await firecrawl_wrappers.firecrawl_research_search_papers(
            caller,
            query="transformer attention mechanism",
            k=3,
        )
        print(
            f"firecrawl_research_search_papers: {type(papers).__name__} "
            f"len={len(str(papers))}"
        )

        # firecrawl_research_inspect_paper -> Any  (observed shape: str)
        inspected = await firecrawl_wrappers.firecrawl_research_inspect_paper(
            caller,
            paperId="10.1038/nature14539",
        )
        print(
            f"firecrawl_research_inspect_paper: {type(inspected).__name__} "
            f"len={len(str(inspected))}"
        )

        # firecrawl_research_read_paper -> Any  (observed shape: str)
        read = await firecrawl_wrappers.firecrawl_research_read_paper(
            caller,
            paperId="arxiv:1908.11775",
            question="How is attention formulated as a kernel?",
            k=3,
        )
        print(
            f"firecrawl_research_read_paper: {type(read).__name__} "
            f"len={len(str(read))}"
        )

        # firecrawl_research_related_papers -> Any  (observed shape: str)
        related = await firecrawl_wrappers.firecrawl_research_related_papers(
            caller,
            seed_ids=["arxiv:1908.11775"],
            intent="find follow-up work on kernel attention",
            mode="similar",
            k=3,
        )
        print(
            f"firecrawl_research_related_papers: {type(related).__name__} "
            f"len={len(str(related))}"
        )

        # firecrawl_research_search_github -> Any  (observed shape: str)
        gh = await firecrawl_wrappers.firecrawl_research_search_github(
            caller,
            query="model context protocol server implementation",
            k=3,
        )
        print(
            f"firecrawl_research_search_github: {type(gh).__name__} "
            f"len={len(str(gh))}"
        )

        # firecrawl_monitor_list -> list  (unwrap: data)
        # The probed account had zero monitors, so the element shape is
        # unobserved and an empty list here is the expected result.
        monitors = await firecrawl_wrappers.firecrawl_monitor_list(caller, limit=5)
        print(f"firecrawl_monitor_list: {len(monitors)} monitor(s)")

        # firecrawl_parse -> ParseUploadTicket
        # The probe used a local scratchpad CSV; point FIRECRAWL_PARSE_FILE at
        # any readable local file to exercise this tool.
        parse_file = os.environ.get("FIRECRAWL_PARSE_FILE")
        if parse_file and os.path.isfile(parse_file):
            ticket = await firecrawl_wrappers.firecrawl_parse(
                caller,
                filePath=parse_file,
                formats=["markdown"],
            )
            print(
                f"firecrawl_parse: success={ticket.get('success')!r} "
                f"mode={ticket.get('mode')!r} "
                f"notes={len(ticket.get('notes') or [])}"
            )
        else:
            print("firecrawl_parse: skipped (set FIRECRAWL_PARSE_FILE to a local file)")


if __name__ == "__main__":
    asyncio.run(main())
