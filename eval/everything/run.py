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

# The wrapper module sits next to this file (eval/everything/everything.py), so
# the script's own directory is what has to go on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import everything

from mcpgen import McpBridgeCaller


async def main() -> None:
    caller = McpBridgeCaller(cmd="npx -y @modelcontextprotocol/server-everything")

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: toggle_simulated_logging,
        # toggle_subscriber_updates (flip server-side logging/subscription
        # state), gzip_file_as_resource (writes a new compressed resource),
        # trigger_long_running_operation (kicks off a stateful async operation
        # with progress notifications). None match the literal mutating-keyword
        # list, but each has a side effect on the server, so they are excluded
        # from this read-only smoke test by semantic judgment.

        # All args below come from eval/everything/everything.verify.json
        # (real, pre-scrub probe args) unless noted otherwise.

        # echo -> Any  (prose payload per shapes.json)
        echoed = await everything.echo(caller, message="hello from mcpgen probe")
        print(f"echo: {type(echoed).__name__}")

        # get_sum -> Any  (prose payload)
        total = await everything.get_sum(caller, a=2, b=3)
        print(f"get_sum: {type(total).__name__}")

        # get_env -> Any  (dict[str, str]; keys are machine-specific)
        env = await everything.get_env(caller)
        print(f"get_env: {type(env).__name__}")

        # get_tiny_image -> Any  (image blocks surface as metadata envelopes)
        image = await everything.get_tiny_image(caller)
        print(f"get_tiny_image: {type(image).__name__}")

        # get_resource_links -> Any  (heterogeneous list: prose, then
        # resource_link dicts)
        links = await everything.get_resource_links(caller, count=3)
        print(f"get_resource_links: {type(links).__name__}")

        # get_resource_reference -> Any  (resourceType='Text', probed variant 1/2)
        ref_text = await everything.get_resource_reference(
            caller, resourceType="Text", resourceId=1
        )
        print(f"get_resource_reference(Text): {type(ref_text).__name__}")

        # get_resource_reference -> Any  (resourceType='Blob', probed variant 2/2)
        ref_blob = await everything.get_resource_reference(
            caller, resourceType="Blob", resourceId=2
        )
        print(f"get_resource_reference(Blob): {type(ref_blob).__name__}")

        # get_annotated_message -> Any  (messageType='error', probed variant 1/3;
        # the literal 'Error: Operation failed' text is the demo's content)
        ann_error = await everything.get_annotated_message(caller, messageType="error")
        print(f"get_annotated_message(error): {type(ann_error).__name__}")

        # get_annotated_message -> Any  (messageType='success', probed variant 2/3)
        ann_success = await everything.get_annotated_message(caller, messageType="success")
        print(f"get_annotated_message(success): {type(ann_success).__name__}")

        # get_annotated_message -> Any  (messageType='debug', probed variant 3/3)
        ann_debug = await everything.get_annotated_message(caller, messageType="debug")
        print(f"get_annotated_message(debug): {type(ann_debug).__name__}")

        # get_structured_content -> WeatherReading  (location='New York', probed variant 1/3)
        weather_ny = await everything.get_structured_content(caller, location="New York")
        print(
            f"get_structured_content(New York): "
            f"temperature={weather_ny.get('temperature')!r}  "
            f"conditions={weather_ny.get('conditions')!r}"
        )

        # get_structured_content -> WeatherReading  (location='Chicago', probed variant 2/3)
        weather_chi = await everything.get_structured_content(caller, location="Chicago")
        print(
            f"get_structured_content(Chicago): "
            f"temperature={weather_chi.get('temperature')!r}  "
            f"conditions={weather_chi.get('conditions')!r}"
        )

        # get_structured_content -> WeatherReading  (location='Los Angeles', probed variant 3/3)
        weather_la = await everything.get_structured_content(caller, location="Los Angeles")
        print(
            f"get_structured_content(Los Angeles): "
            f"temperature={weather_la.get('temperature')!r}  "
            f"conditions={weather_la.get('conditions')!r}"
        )

        # simulate_research_query -> Any  (no entry in verify.json — these args
        # are schema-minimal/synthetic; ambiguous=False avoids an elicitation
        # round-trip this runner cannot answer)
        research = await everything.simulate_research_query(
            caller, topic="renewable energy trends", ambiguous=False
        )
        print(f"simulate_research_query: {type(research).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
