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

    # Skipped mutating tools: toggle_simulated_logging, toggle_subscriber_updates,
    #                         trigger_long_running_operation, gzip_file_as_resource

    # get_env -> Any
    env = await everything.get_env(caller)
    print(f"get_env: {type(env).__name__}")

    # echo -> Any
    echoed = await everything.echo(caller, message="hello world")
    print(f"echo: {type(echoed).__name__}")

    # get_sum -> Any
    total = await everything.get_sum(caller, a=3, b=4)
    print(f"get_sum: {type(total).__name__}")

    # get_tiny_image -> Any
    image = await everything.get_tiny_image(caller)
    print(f"get_tiny_image: {type(image).__name__}")

    # get_resource_links -> Any
    links = await everything.get_resource_links(caller, count=3)
    print(f"get_resource_links: {type(links).__name__}")

    # get_resource_reference -> Any  (resourceType='Text')
    ref_text = await everything.get_resource_reference(caller, resourceType="Text", resourceId=1)
    print(f"get_resource_reference(Text): {type(ref_text).__name__}")

    # get_resource_reference -> Any  (resourceType='Blob')
    ref_blob = await everything.get_resource_reference(caller, resourceType="Blob", resourceId=1)
    print(f"get_resource_reference(Blob): {type(ref_blob).__name__}")

    # get_annotated_message -> Any  (messageType='error')
    ann_error = await everything.get_annotated_message(caller, messageType="error")
    print(f"get_annotated_message(error): {type(ann_error).__name__}")

    # get_annotated_message -> Any  (messageType='success')
    ann_success = await everything.get_annotated_message(caller, messageType="success")
    print(f"get_annotated_message(success): {type(ann_success).__name__}")

    # get_annotated_message -> Any  (messageType='debug')
    ann_debug = await everything.get_annotated_message(caller, messageType="debug")
    print(f"get_annotated_message(debug): {type(ann_debug).__name__}")

    # get_structured_content -> WeatherContent  (location='New York')
    weather_ny = await everything.get_structured_content(caller, location="New York")
    print(f"get_structured_content(New York): temperature={weather_ny.get('temperature')!r}  conditions={weather_ny.get('conditions')!r}")

    # get_structured_content -> WeatherContent  (location='Chicago')
    weather_chi = await everything.get_structured_content(caller, location="Chicago")
    print(f"get_structured_content(Chicago): temperature={weather_chi.get('temperature')!r}  conditions={weather_chi.get('conditions')!r}")

    # get_structured_content -> WeatherContent  (location='Los Angeles')
    weather_la = await everything.get_structured_content(caller, location="Los Angeles")
    print(f"get_structured_content(Los Angeles): temperature={weather_la.get('temperature')!r}  conditions={weather_la.get('conditions')!r}")

    # simulate_research_query -> Any
    research = await everything.simulate_research_query(caller, topic="climate change")
    print(f"simulate_research_query: {type(research).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
