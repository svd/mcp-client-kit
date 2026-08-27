"""
Smoke-test runner for generated aws-knowledge/ wrappers.
Transport: Streamable HTTP  (https://knowledge-mcp.global.api.aws)
Auth: none (public endpoint)

Args come from aws-knowledge.verify.json (real, pre-scrub probe args), falling
back to aws-knowledge.shapes.json probed_args where verify.json records only one
of several probed forms.

No tool in aws-knowledge.shapes.json declares a `discriminator`/`variants` block,
so each tool gets one call — except aws___search_documentation, whose shape entry
records two probed arg sets (`topics=['general']` and `topics=['agent_skills']`)
that return different record shapes unioned into one total=False model. Both are
called so each probed response shape is exercised.

Usage:
    python eval/aws-knowledge/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "aws-knowledge.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path as `aws_knowledge`.
_spec = importlib.util.spec_from_file_location(
    "aws_knowledge",
    os.path.join(os.path.dirname(__file__), "aws-knowledge.py"),
)
assert _spec is not None and _spec.loader is not None
aws_knowledge = importlib.util.module_from_spec(_spec)
sys.modules["aws_knowledge"] = aws_knowledge
_spec.loader.exec_module(aws_knowledge)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://knowledge-mcp.global.api.aws"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — every aws-knowledge tool is read-only.

        # aws___list_regions -> list[Region]  (no args)
        regions = await aws_knowledge.aws___list_regions(caller)
        print(f"list_regions: {len(regions)} region(s)")
        if regions:
            first = regions[0]
            print(
                f"  first: region_id={first.get('region_id')!r} "
                f"region_long_name={first.get('region_long_name')!r}"
            )

        # aws___get_regional_availability -> Any
        # unwrap stops at content.result: a map keyed by AWS product name ->
        # {status: str}. The outer key under `result` varies with resource_type
        # (products | service_apis | cfn_resources), so nothing below result is
        # typed. Args from verify.json.
        availability = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="product",
            regions=["us-east-1"],
        )
        print(f"get_regional_availability(product): {type(availability).__name__}")
        if isinstance(availability, dict):
            keys = list(availability)
            print(f"  {len(keys)} key(s); first: {keys[:3]!r}")

        # aws___search_documentation -> list[SearchResultItem]
        # Probed form 1 (shapes.json probed_args[0]): doc topics -> records carry
        # {rank_order, title, context, url}.
        doc_hits = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="S3 bucket versioning",
            limit=3,
            topics=["general"],
        )
        print(f"search_documentation(general): {len(doc_hits)} result(s)")
        if doc_hits:
            top = doc_hits[0]
            print(
                f"  top: rank_order={top.get('rank_order')!r} "
                f"title={top.get('title')!r} url={top.get('url')!r}"
            )

        # aws___search_documentation -> list[SearchResultItem]
        # Probed form 2 (verify.json): topics=['agent_skills'] -> records carry
        # {rank_order, title, skill_description, skill_name} instead.
        skill_hits = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="deploy serverless application skill workflow",
            limit=5,
            topics=["agent_skills"],
        )
        print(f"search_documentation(agent_skills): {len(skill_hits)} result(s)")
        if skill_hits:
            top_skill = skill_hits[0]
            print(
                f"  top: rank_order={top_skill.get('rank_order')!r} "
                f"skill_name={top_skill.get('skill_name')!r} "
                f"skill_description={top_skill.get('skill_description')!r}"
            )

        # aws___retrieve_skill -> Any
        # unwrap is content.skill_content: the SKILL.md markdown as a plain
        # string, not a record. Args from verify.json.
        skill = await aws_knowledge.aws___retrieve_skill(
            caller,
            skill_name="aws-serverless",
        )
        print(f"retrieve_skill: {type(skill).__name__}")
        if isinstance(skill, str):
            print(f"  {len(skill)} char(s); starts: {skill[:60]!r}")

        # aws___read_documentation -> list[DocumentationPage]
        # Args from verify.json.
        pages = await aws_knowledge.aws___read_documentation(
            caller,
            requests=[
                {
                    "url": "https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html",
                    "max_length": 2000,
                }
            ],
        )
        print(f"read_documentation: {len(pages)} page(s)")
        if pages:
            page = pages[0]
            print(
                f"  page: status={page.get('status')!r} "
                f"url={page.get('url')!r} "
                f"total_length={page.get('total_length')} "
                f"truncated={page.get('truncated')} "
                f"error_code={page.get('error_code')!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
