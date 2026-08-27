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

    # One connection for the whole run: a single initialize() and a single
    # subprocess, instead of reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: gzip_file_as_resource (writes a resource),
        # toggle_simulated_logging, toggle_subscriber_updates (flip server state).
        # All args below come from eval/everything/everything.verify.json (real
        # probed args) except where marked synthetic.

        # echo -> Any  (prose echo of the message)
        echoed = await everything.echo(caller, message="eval-probe")
        print(f"echo: {type(echoed).__name__}")

        # get-sum -> Any  (prose: "The sum of 2 and 3 is 5.")
        total = await everything.get_sum(caller, a=2, b=3)
        print(f"get-sum: {type(total).__name__}")

        # get-env -> Any  (flat str->str mapping of the launched process env)
        env = await everything.get_env(caller)
        print(f"get-env: {type(env).__name__}")

        # get-annotated-message -> Any  (messageType="error")
        annotated_error = await everything.get_annotated_message(caller, messageType="error")
        print(f"get-annotated-message(error): {type(annotated_error).__name__}")

        # get-annotated-message -> Any  (messageType="success")
        annotated_success = await everything.get_annotated_message(caller, messageType="success")
        print(f"get-annotated-message(success): {type(annotated_success).__name__}")

        # get-annotated-message -> Any  (messageType="debug")
        annotated_debug = await everything.get_annotated_message(caller, messageType="debug")
        print(f"get-annotated-message(debug): {type(annotated_debug).__name__}")

        # get-resource-links -> Any  (resource_link blocks, metadata dropped by mcpgen)
        links = await everything.get_resource_links(caller, count=3)
        print(f"get-resource-links: {type(links).__name__}")

        # get-resource-reference -> Any  (resourceType is an enum, but both
        # "Text" and "Blob" were probed and returned the same shape; verify.json
        # keeps the "Blob" probe, so only that one is replayed here)
        ref = await everything.get_resource_reference(caller, resourceType="Blob", resourceId=2)
        print(f"get-resource-reference: {type(ref).__name__}")

        # get-tiny-image -> Any  (base64 image bytes dropped by the probe)
        image = await everything.get_tiny_image(caller)
        print(f"get-tiny-image: {type(image).__name__}")

        # get-structured-content -> WeatherRecord  (the only shaped tool here)
        weather = await everything.get_structured_content(caller, location="New York")
        print(
            f"get-structured-content: temperature={weather.get('temperature')!r} "
            f"conditions={weather.get('conditions')!r} humidity={weather.get('humidity')!r}"
        )

        # trigger-long-running-operation -> Any  (prose completion summary)
        long_op = await everything.trigger_long_running_operation(caller, duration=1, steps=2)
        print(f"trigger-long-running-operation: {type(long_op).__name__}")

        # simulate-research-query -> Any  (not probed; synthetic args.
        # ambiguous=False keeps it from raising an elicitation request.)
        research = await everything.simulate_research_query(
            caller, topic="model context protocol", ambiguous=False
        )
        print(f"simulate-research-query: {type(research).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
