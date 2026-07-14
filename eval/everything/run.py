"""
Smoke-test runner for generated everything/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-everything)
Auth: none

Usage:
    python eval/everything/run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import everything

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-everything")

    # Skipped mutating tools: toggle_simulated_logging, toggle_subscriber_updates
    # (flip server-side logging/subscription state), trigger_long_running_operation
    # (kicks off a stateful async operation), gzip_file_as_resource (writes a new
    # compressed resource). None of these match the literal mutating-keyword list
    # (create/update/delete/.../assign), but each has a side effect on the server,
    # so they are excluded from this read-only smoke test by semantic judgment.

    # echo -> Any
    echoed = await everything.echo(caller, message="hello from eval")
    print(f"echo: {type(echoed).__name__}")

    # get_env -> Any
    env = await everything.get_env(caller)
    print(f"get_env: {type(env).__name__}")

    # get_sum -> Any
    total = await everything.get_sum(caller, a=2, b=3)
    print(f"get_sum: {type(total).__name__}")

    # get_tiny_image -> Any
    image = await everything.get_tiny_image(caller)
    print(f"get_tiny_image: {type(image).__name__}")

    # get_resource_links -> Any
    links = await everything.get_resource_links(caller, count=3)
    print(f"get_resource_links: {type(links).__name__}")

    # get_resource_reference -> Any  (resourceType='Text', probed variant 1/2)
    ref_text = await everything.get_resource_reference(caller, resourceType="Text", resourceId=1)
    print(f"get_resource_reference(Text): {type(ref_text).__name__}")

    # get_resource_reference -> Any  (resourceType='Blob', probed variant 2/2)
    ref_blob = await everything.get_resource_reference(caller, resourceType="Blob", resourceId=1)
    print(f"get_resource_reference(Blob): {type(ref_blob).__name__}")

    # get_annotated_message -> Any  (messageType='error', probed variant 1/3)
    ann_error = await everything.get_annotated_message(caller, messageType="error")
    print(f"get_annotated_message(error): {type(ann_error).__name__}")

    # get_annotated_message -> Any  (messageType='success', probed variant 2/3)
    ann_success = await everything.get_annotated_message(caller, messageType="success")
    print(f"get_annotated_message(success): {type(ann_success).__name__}")

    # get_annotated_message -> Any  (messageType='debug', probed variant 3/3)
    ann_debug = await everything.get_annotated_message(caller, messageType="debug")
    print(f"get_annotated_message(debug): {type(ann_debug).__name__}")

    # get_structured_content -> WeatherConditions  (location='New York'; only
    # variant present in verify.json — 'Chicago'/'Los Angeles' were not probed)
    weather_ny = await everything.get_structured_content(caller, location="New York")
    print(f"get_structured_content(New York): temperature={weather_ny.get('temperature')!r}  conditions={weather_ny.get('conditions')!r}")

    # simulate_research_query -> Any
    research = await everything.simulate_research_query(caller, topic="renewable energy trends", ambiguous=False)
    print(f"simulate_research_query: {type(research).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
