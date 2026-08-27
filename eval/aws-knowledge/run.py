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
# The wrapper module file is "aws-knowledge.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path as `aws_knowledge`.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "aws-knowledge.py")
_spec = importlib.util.spec_from_file_location("aws_knowledge", _MODULE_PATH)
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
        # Args come from aws-knowledge.verify.json (real, pre-scrub); the
        # product/api variants of get_regional_availability and the first
        # search_documentation probe come from shapes.json probed_args, which
        # carry no placeholders for this server.

        # aws___list_regions -> list[AwsRegion]  (no parameters)
        regions = await aws_knowledge.aws___list_regions(caller)
        print(f"aws___list_regions: {len(regions)} region(s)")

        # aws___get_regional_availability -> RegionalProductAvailability  (resource_type="product")
        avail_product = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="product",
            regions=["us-east-1"],
        )
        print(
            "aws___get_regional_availability(product): "
            f"products={len(avail_product.get('products') or {})}  "
            f"next_token={bool(avail_product.get('next_token'))}"
        )

        # aws___get_regional_availability -> RegionalApiAvailability  (resource_type="api")
        avail_api = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="api",
            regions=["us-east-1"],
        )
        print(
            "aws___get_regional_availability(api): "
            f"service_apis={len(avail_api.get('service_apis') or {})}  "
            f"next_token={bool(avail_api.get('next_token'))}"
        )

        # aws___get_regional_availability -> RegionalCfnAvailability  (resource_type="cfn")
        avail_cfn = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="cfn",
            regions=["us-east-1"],
        )
        print(
            "aws___get_regional_availability(cfn): "
            f"cfn_resources={len(avail_cfn.get('cfn_resources') or {})}  "
            f"next_token={bool(avail_cfn.get('next_token'))}"
        )

        # aws___search_documentation -> list[SearchResultItem]  (no topics filter)
        hits = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="Lambda function timeout configuration",
            limit=3,
        )
        print(f"aws___search_documentation: {len(hits)} hit(s)")
        if hits:
            top = hits[0]
            # Heterogeneous list: doc hits carry title/url, agent-skill hits
            # carry skill_name/skill_description. Both are total=False.
            print(
                f"  top: rank={top.get('rank_order')!r} "
                f"title={top.get('title')!r} skill={top.get('skill_name')!r}"
            )

        # aws___search_documentation -> list[SearchResultItem]  (topics=["troubleshooting"])
        hits_topic = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="Lambda timeout error",
            topics=["troubleshooting"],
            limit=3,
        )
        print(f"aws___search_documentation(troubleshooting): {len(hits_topic)} hit(s)")

        # aws___read_documentation -> list[DocPage]
        pages = await aws_knowledge.aws___read_documentation(
            caller,
            requests=[
                {
                    "url": "https://docs.aws.amazon.com/lambda/latest/dg/welcome.html",
                    "max_length": 2000,
                }
            ],
        )
        print(f"aws___read_documentation: {len(pages)} page(s)")
        if pages:
            page = pages[0]
            print(
                f"  status={page.get('status')!r} url={page.get('url')!r} "
                f"total_length={page.get('total_length')} "
                f"truncated={page.get('truncated')}"
            )

        # aws___retrieve_skill -> Any  (unwrapped to a markdown string)
        skill = await aws_knowledge.aws___retrieve_skill(
            caller,
            skill_name="debugging-lambda-timeouts",
        )
        print(
            f"aws___retrieve_skill: {type(skill).__name__} "
            f"({len(skill) if isinstance(skill, str) else 'n/a'} chars)"
        )


if __name__ == "__main__":
    asyncio.run(main())
