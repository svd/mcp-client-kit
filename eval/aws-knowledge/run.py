"""
Smoke-test runner for generated aws-knowledge/ wrappers.
Transport: Streamable HTTP  (https://knowledge-mcp.global.api.aws)
Auth: none (public endpoint)

Args come from aws-knowledge.verify.json (real, pre-scrub probe args).
aws___get_regional_availability declares a discriminator (resource_type) with
three variants; only "cfn" was probed, so the "api" and "product" calls below
use no `filters` — the catalog-listing form the tool docs prescribe when exact
filter names are unknown — rather than guessed filter values.

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

        # aws___list_regions -> list[Region]
        regions = await aws_knowledge.aws___list_regions(caller)
        print(f"list_regions: {len(regions)} region(s)")
        if regions:
            first = regions[0]
            print(
                f"  first: region_id={first.get('region_id')!r} "
                f"region_long_name={first.get('region_long_name')!r}"
            )

        # aws___search_documentation -> list[SearchResultItem]  (general topic)
        docs_hits = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="S3 bucket versioning",
            limit=3,
        )
        print(f"search_documentation(general): {len(docs_hits)} result(s)")
        if docs_hits:
            top = docs_hits[0]
            print(
                f"  top: rank_order={top.get('rank_order')!r} "
                f"title={top.get('title')!r} url={top.get('url')!r}"
            )

        # aws___search_documentation -> list[SearchResultItem]  (agent_skills topic)
        skill_hits = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="lambda best practices skill",
            topics=["agent_skills"],
            limit=3,
        )
        print(f"search_documentation(agent_skills): {len(skill_hits)} result(s)")
        if skill_hits:
            top_skill = skill_hits[0]
            print(
                f"  top: skill_name={top_skill.get('skill_name')!r} "
                f"skill_description={top_skill.get('skill_description')!r}"
            )

        # aws___read_documentation -> list[DocumentationPage]
        pages = await aws_knowledge.aws___read_documentation(
            caller,
            requests=[
                {
                    "url": "https://docs.aws.amazon.com/code-library/latest/ug/java_2_s3_code_examples.html",
                    "max_length": 2000,
                }
            ],
        )
        print(f"read_documentation: {len(pages)} page(s)")
        if pages:
            page = pages[0]
            print(
                f"  page: status={page.get('status')!r} "
                f"total_length={page.get('total_length')} "
                f"truncated={page.get('truncated')} "
                f"error_code={page.get('error_code')!r}"
            )

        # aws___retrieve_skill -> Any  (skill_name is an opaque registry ID)
        skill = await aws_knowledge.aws___retrieve_skill(
            caller,
            skill_name="aws-lambda-durable-functions",
        )
        print(f"retrieve_skill: {type(skill).__name__}")

        # aws___get_regional_availability -> CfnResourceAvailability  (resource_type='cfn')
        cfn = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="cfn",
            regions=["us-east-1"],
            filters=["AWS::Lambda::Function"],
        )
        print(
            f"get_regional_availability(cfn): "
            f"{len(cfn.get('cfn_resources') or {})} resource(s) "
            f"next_token={cfn.get('next_token')!r} "
            f"failed_regions={cfn.get('failed_regions')!r}"
        )

        # aws___get_regional_availability -> ServiceApiAvailability  (resource_type='api')
        # Variant not probed: single region, no `filters` — the catalog-listing
        # form the tool docs prescribe when exact filter names are unknown.
        api = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="api",
            regions=["us-east-1"],
        )
        print(
            f"get_regional_availability(api): "
            f"{len(api.get('service_apis') or {})} api(s) "
            f"next_token={api.get('next_token')!r} "
            f"failed_regions={api.get('failed_regions')!r}"
        )

        # aws___get_regional_availability -> ProductAvailability  (resource_type='product')
        # Variant not probed: single region, no `filters` (see note above).
        product = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="product",
            regions=["us-east-1"],
        )
        print(
            f"get_regional_availability(product): "
            f"{len(product.get('products') or {})} product(s) "
            f"next_token={product.get('next_token')!r} "
            f"failed_regions={product.get('failed_regions')!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
