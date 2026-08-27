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
        # Skipped mutating / job-creating tools: firecrawl_agent, firecrawl_crawl,
        # firecrawl_feedback, firecrawl_interact, firecrawl_interact_stop,
        # firecrawl_monitor_create, firecrawl_monitor_delete, firecrawl_monitor_run,
        # firecrawl_monitor_update, firecrawl_search_feedback
        # No tool in firecrawl.shapes.json carries a discriminator, so every tool
        # below is exercised once. Args come from firecrawl.verify.json (real,
        # pre-scrub values).

        # firecrawl_map -> list[MapLink]
        links = await firecrawl.firecrawl_map(
            caller, url="https://example.com", limit=5
        )
        print(f"firecrawl_map: {len(links)} link(s)")

        # firecrawl_scrape -> ScrapeResult
        scraped = await firecrawl.firecrawl_scrape(
            caller, url="https://example.com", formats=["markdown"]
        )
        print(
            f"firecrawl_scrape: markdown_len="
            f"{len(scraped.get('markdown') or '')}  "
            f"metadata_keys={sorted((scraped.get('metadata') or {}).keys())[:3]}"
        )

        # firecrawl_search -> SearchResponse
        found = await firecrawl.firecrawl_search(
            caller, query="model context protocol", limit=3
        )
        print(
            f"firecrawl_search: success={found.get('success')!r}  "
            f"id={found.get('id')!r}  creditsUsed={found.get('creditsUsed')!r}"
        )

        # firecrawl_developer_search -> Any  (probe returned prose)
        dev = await firecrawl.firecrawl_developer_search(
            caller, query="python asyncio TaskGroup cancellation semantics", k=3
        )
        print(f"firecrawl_developer_search: {type(dev).__name__}")

        # firecrawl_research_search_papers -> Any  (probe returned prose)
        papers = await firecrawl.firecrawl_research_search_papers(
            caller, query="retrieval augmented generation", k=3
        )
        print(f"firecrawl_research_search_papers: {type(papers).__name__}")

        # firecrawl_research_search_github -> Any  (probe returned prose)
        repos = await firecrawl.firecrawl_research_search_github(
            caller, query="model context protocol server", k=3
        )
        print(f"firecrawl_research_search_github: {type(repos).__name__}")

        # firecrawl_research_inspect_paper -> Any  (probe returned prose)
        paper = await firecrawl.firecrawl_research_inspect_paper(
            caller, paperId="arxiv:2503.23278"
        )
        print(f"firecrawl_research_inspect_paper: {type(paper).__name__}")

        # firecrawl_research_read_paper -> Any  (probe returned prose)
        passage = await firecrawl.firecrawl_research_read_paper(
            caller,
            paperId="arxiv:2503.23278",
            question="What security threats are identified?",
            k=2,
        )
        print(f"firecrawl_research_read_paper: {type(passage).__name__}")

        # firecrawl_research_related_papers -> Any  (probe returned prose)
        related = await firecrawl.firecrawl_research_related_papers(
            caller,
            seed_ids=["arxiv:2503.23278"],
            intent="find related work on MCP security",
            mode="similar",
            k=3,
        )
        print(f"firecrawl_research_related_papers: {type(related).__name__}")

        # firecrawl_parse -> ParseResponse
        # filePath below is the real probe path from firecrawl.verify.json; it lives
        # in a machine-local scratchpad, so point it at any local HTML/PDF file.
        parsed = await firecrawl.firecrawl_parse(
            caller,
            filePath=(
                "/private/tmp/claude-501/"
                "-Users-Sviataslau-Svirydau-src-mcp-client-kit-eval/"
                "ddfba483-0e92-4952-8aea-f18a53d6bdf6/scratchpad/probe-doc.html"
            ),
            formats=["markdown"],
        )
        print(
            f"firecrawl_parse: success={parsed.get('success')!r}  "
            f"mode={parsed.get('mode')!r}  message={parsed.get('message')!r}"
        )

        # firecrawl_monitor_list -> Any  (list container, unwrapped from .data)
        monitors = await firecrawl.firecrawl_monitor_list(caller, limit=5)
        print(f"firecrawl_monitor_list: {len(monitors)} monitor(s)")

        # The four id-addressed tools below were probed with placeholder UUIDs and
        # returned no success payload (_probe_status: inconclusive). Substitute a real
        # monitor / crawl / agent id to get a meaningful shape out of them.
        placeholder_id = "00000000-0000-4000-8000-000000000000"

        # firecrawl_monitor_get -> Any
        monitor = await firecrawl.firecrawl_monitor_get(caller, id=placeholder_id)
        print(f"firecrawl_monitor_get: {type(monitor).__name__}")

        # firecrawl_monitor_checks -> Any
        checks = await firecrawl.firecrawl_monitor_checks(
            caller, id=placeholder_id, limit=3
        )
        print(f"firecrawl_monitor_checks: {type(checks).__name__}")

        # firecrawl_monitor_check -> Any
        check = await firecrawl.firecrawl_monitor_check(
            caller, id=placeholder_id, checkId=placeholder_id
        )
        print(f"firecrawl_monitor_check: {type(check).__name__}")

        # firecrawl_check_crawl_status -> Any
        crawl_status = await firecrawl.firecrawl_check_crawl_status(
            caller, id=placeholder_id
        )
        print(f"firecrawl_check_crawl_status: {type(crawl_status).__name__}")

        # firecrawl_agent_status -> Any
        agent_status = await firecrawl.firecrawl_agent_status(
            caller, id=placeholder_id
        )
        print(f"firecrawl_agent_status: {type(agent_status).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
