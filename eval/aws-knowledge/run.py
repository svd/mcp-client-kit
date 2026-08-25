"""
Smoke-test runner for generated aws-knowledge/ wrappers.
Transport: Streamable HTTP  (https://knowledge-mcp.global.api.aws)
Auth: none (public endpoint)

Usage:
    python eval/aws-knowledge/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "aws-knowledge.py" — the hyphen makes it unimportable
# by a plain `import`, so load it by path under the name `aws_knowledge`.
_spec = importlib.util.spec_from_file_location(
    "aws_knowledge", os.path.join(os.path.dirname(__file__), "aws-knowledge.py")
)
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
        # Skipped mutating tools: (none — every aws-knowledge tool is read-only)
        # Args below come from eval/aws-knowledge/aws-knowledge.verify.json
        # (real pre-scrub probe args), except list_regions which takes none.

        # aws___list_regions -> list[Region]
        regions = await aws_knowledge.aws___list_regions(caller)
        print(f"aws___list_regions: {len(regions)} region(s)")
        if regions:
            print(
                f"  first: region_id={regions[0].get('region_id')!r} "
                f"region_long_name={regions[0].get('region_long_name')!r}"
            )

        # aws___get_regional_availability -> RegionalAvailability  (resource_type="product")
        avail_product = await aws_knowledge.aws___get_regional_availability(
            caller, resource_type="product", regions=["us-east-1"]
        )
        print(
            f"aws___get_regional_availability(product): "
            f"products={len(avail_product.get('products') or {})} entry(ies)  "
            f"next_token={avail_product.get('next_token')!r}"
        )

        # aws___get_regional_availability -> RegionalAvailability  (resource_type="api")
        avail_api = await aws_knowledge.aws___get_regional_availability(
            caller, resource_type="api", regions=["us-east-1"]
        )
        print(
            f"aws___get_regional_availability(api): "
            f"service_apis={len(avail_api.get('service_apis') or {})} entry(ies)  "
            f"next_token={avail_api.get('next_token')!r}"
        )

        # aws___get_regional_availability -> RegionalAvailability  (resource_type="cfn")
        avail_cfn = await aws_knowledge.aws___get_regional_availability(
            caller, resource_type="cfn", regions=["us-east-1"]
        )
        print(
            f"aws___get_regional_availability(cfn): "
            f"cfn_resources={len(avail_cfn.get('cfn_resources') or {})} entry(ies)  "
            f"next_token={avail_cfn.get('next_token')!r}"
        )

        # aws___search_documentation -> list[SearchResultItem]  (docs search)
        docs_hits = await aws_knowledge.aws___search_documentation(
            caller, search_phrase="S3 bucket versioning", limit=3
        )
        print(f"aws___search_documentation(docs): {len(docs_hits)} hit(s)")
        if docs_hits:
            print(
                f"  top: rank_order={docs_hits[0].get('rank_order')!r} "
                f"title={docs_hits[0].get('title')!r} url={docs_hits[0].get('url')!r}"
            )

        # aws___search_documentation -> list[SearchResultItem]  (topics=["agent_skills"])
        skill_hits = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="lambda deployment skill",
            topics=["agent_skills"],
            limit=3,
        )
        print(f"aws___search_documentation(agent_skills): {len(skill_hits)} hit(s)")
        if skill_hits:
            print(
                f"  top: skill_name={skill_hits[0].get('skill_name')!r} "
                f"skill_description={skill_hits[0].get('skill_description')!r}"
            )

        # aws___read_documentation -> list[DocumentationPage]
        pages = await aws_knowledge.aws___read_documentation(
            caller,
            requests=[
                {
                    "url": "https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html",
                    "max_length": 2000,
                }
            ],
        )
        print(f"aws___read_documentation: {len(pages)} page(s)")
        if pages:
            print(
                f"  first: status={pages[0].get('status')!r} "
                f"total_length={pages[0].get('total_length')!r} "
                f"truncated={pages[0].get('truncated')!r}"
            )

        # aws___retrieve_skill -> SkillDocument
        skill = await aws_knowledge.aws___retrieve_skill(caller, skill_name="aws-serverless")
        print(
            f"aws___retrieve_skill: skill_content={len(skill.get('skill_content') or '')} char(s)"
        )


if __name__ == "__main__":
    asyncio.run(main())
