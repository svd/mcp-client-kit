"""
Smoke-test runner for generated everything/ wrappers.
Transport: stdio  (npx -y @modelcontextprotocol/server-everything)
Auth: none

Usage:
    python eval/everything/run.py

The everything server is a protocol demo: most tools return prose, images, or
resource links rather than structured records. Only get-structured-content has
a shaped return (WeatherReport), and it is called once per probed location.
"""
import asyncio
import os
import sys

# The wrapper module sits next to this file; put its directory on sys.path so
# "import everything" resolves to everything.py rather than to the package
# directory that holds it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import everything

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-everything")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: gzip_file_as_resource (writes a new
        # server-side resource), toggle_simulated_logging,
        # toggle_subscriber_updates (both flip server session state).

        # get-env -> Any
        env = await everything.get_env(caller)
        print(f"get_env: {type(env).__name__}")

        # echo -> Any
        echoed = await everything.echo(caller, message="eval probe")
        print(f"echo: {type(echoed).__name__}")

        # get-sum -> Any
        total = await everything.get_sum(caller, a=2, b=3)
        print(f"get_sum: {type(total).__name__}")

        # get-annotated-message -> Any
        annotated = await everything.get_annotated_message(caller, messageType="debug")
        print(f"get_annotated_message: {type(annotated).__name__}")

        # get-tiny-image -> Any  (no args in schema; not probed with args)
        image = await everything.get_tiny_image(caller)
        print(f"get_tiny_image: {type(image).__name__}")

        # get-resource-links -> Any
        links = await everything.get_resource_links(caller, count=3)
        print(f"get_resource_links: {type(links).__name__}")

        # get-resource-reference -> Any
        reference = await everything.get_resource_reference(
            caller, resourceType="Blob", resourceId=2
        )
        print(f"get_resource_reference: {type(reference).__name__}")

        # get-structured-content -> WeatherReport  (location='New York')
        weather_ny = await everything.get_structured_content(caller, location="New York")
        print(
            f"get_structured_content(New York): "
            f"temperature={weather_ny.get('temperature')!r} "
            f"conditions={weather_ny.get('conditions')!r} "
            f"humidity={weather_ny.get('humidity')!r}"
        )

        # get-structured-content -> WeatherReport  (location='Chicago')
        weather_chi = await everything.get_structured_content(caller, location="Chicago")
        print(
            f"get_structured_content(Chicago): "
            f"temperature={weather_chi.get('temperature')!r} "
            f"conditions={weather_chi.get('conditions')!r} "
            f"humidity={weather_chi.get('humidity')!r}"
        )

        # get-structured-content -> WeatherReport  (location='Los Angeles')
        weather_la = await everything.get_structured_content(
            caller, location="Los Angeles"
        )
        print(
            f"get_structured_content(Los Angeles): "
            f"temperature={weather_la.get('temperature')!r} "
            f"conditions={weather_la.get('conditions')!r} "
            f"humidity={weather_la.get('humidity')!r}"
        )

        # trigger-long-running-operation -> Any
        # Probed with the short duration/steps pair so the smoke test stays fast.
        operation = await everything.trigger_long_running_operation(
            caller, duration=1, steps=2
        )
        print(f"trigger_long_running_operation: {type(operation).__name__}")

        # simulate-research-query -> Any
        # Not probed during wrapper generation, so these args are synthetic.
        # ambiguous stays False: True triggers an elicitation round-trip that
        # this non-interactive runner cannot answer.
        research = await everything.simulate_research_query(
            caller, topic="model context protocol", ambiguous=False
        )
        print(f"simulate_research_query: {type(research).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
