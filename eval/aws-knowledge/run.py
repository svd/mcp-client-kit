"""
Smoke-test runner for generated aws-knowledge/ wrappers.
Transport: Streamable HTTP  (https://knowledge-mcp.global.api.aws)
Auth: none (public endpoint)

Usage:
    python eval/aws-knowledge/run.py

Args come from aws-knowledge.verify.json (real, pre-scrub probe args).

Note: the generated wrapper module lives at `aws-knowledge.py`, whose filename
contains a hyphen and so cannot be imported with a plain `import` statement.
We load it directly from its file path under a valid identifier to avoid a
SyntaxError/ModuleNotFoundError.
"""
import asyncio
import importlib.util
import os

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aws-knowledge.py")
_spec = importlib.util.spec_from_file_location("aws_knowledge_wrappers", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
aws_knowledge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aws_knowledge)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://knowledge-mcp.global.api.aws"


def _preview(text: object, limit: int = 100) -> str:
    s = "" if text is None else str(text)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit] + "..."


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none - every tool on this server is read-only)

        # aws___list_regions -> list[RegionSummary]
        regions = await aws_knowledge.aws___list_regions(caller)
        print(f"aws___list_regions: {len(regions)} region(s)")
        if regions:
            first = regions[0]
            print(
                f"  first: region_id={first.get('region_id')!r} "
                f"region_long_name={first.get('region_long_name')!r}"
            )

        # aws___search_documentation -> list[SearchDocumentationItem]  (probe 1: plain phrase)
        docs = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="AWS Lambda function timeout limit",
            limit=4,
        )
        print(f"aws___search_documentation(docs): {len(docs)} hit(s)")
        for hit in docs[:2]:
            print(
                f"  rank={hit.get('rank_order')} title={_preview(hit.get('title'), 60)!r} "
                f"url={hit.get('url')!r}"
            )

        # aws___search_documentation -> list[SearchDocumentationItem]  (probe 2: topics=agent_skills)
        skills = await aws_knowledge.aws___search_documentation(
            caller,
            search_phrase="AWS Lambda skill",
            topics=["agent_skills"],
            limit=4,
        )
        print(f"aws___search_documentation(skills): {len(skills)} hit(s)")
        for hit in skills[:2]:
            print(
                f"  skill_name={hit.get('skill_name')!r} "
                f"skill_description={_preview(hit.get('skill_description'), 60)!r}"
            )

        # aws___read_documentation -> list[DocumentationPage]
        pages = await aws_knowledge.aws___read_documentation(
            caller,
            requests=[
                {
                    "url": "https://docs.aws.amazon.com/lambda/latest/dg/welcome.html",
                    "max_length": 3000,
                }
            ],
        )
        print(f"aws___read_documentation: {len(pages)} page(s)")
        for page in pages:
            print(
                f"  status={page.get('status')!r} url={page.get('url')!r} "
                f"total_length={page.get('total_length')} truncated={page.get('truncated')}"
            )
            print(f"  content: {_preview(page.get('content'), 100)!r}")

        # aws___retrieve_skill -> SkillDocument
        skill = await aws_knowledge.aws___retrieve_skill(
            caller,
            skill_name="connecting-lambda-to-api-gateway",
        )
        print(f"aws___retrieve_skill: content={_preview(skill.get('skill_content'), 100)!r}")

        # aws___get_regional_availability -> CfnResourceAvailability  (resource_type="cfn")
        # Only the "cfn" discriminator variant was probed; the "api" and "product"
        # variants have no recorded real args, so they are not exercised here.
        cfn = await aws_knowledge.aws___get_regional_availability(
            caller,
            resource_type="cfn",
            regions=["us-east-1"],
            filters=["AWS::S3::Bucket"],
        )
        cfn_resources = cfn.get("cfn_resources") or {}
        print(
            f"aws___get_regional_availability(cfn): {len(cfn_resources)} region key(s) "
            f"next_token={cfn.get('next_token')!r} failed_regions={cfn.get('failed_regions')!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
