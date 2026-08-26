"""
Smoke-test runner for generated openzeppelin-stylus/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/stylus/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-stylus/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "openzeppelin-stylus.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path as `openzeppelin_stylus`.
_spec = importlib.util.spec_from_file_location(
    "openzeppelin_stylus",
    os.path.join(os.path.dirname(__file__), "openzeppelin-stylus.py"),
)
openzeppelin_stylus = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_stylus"] = openzeppelin_stylus
_spec.loader.exec_module(openzeppelin_stylus)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/stylus/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — every tool is a pure contract-source
        # generator: "Returns the source code ... Does not write to disk.")
        # Args below are the real pre-scrub probe args from
        # openzeppelin-stylus.verify.json. No tool carries a discriminator in
        # shapes.json, so one call per tool covers its return shape.
        # All three tools return prose (a Markdown code block), so shapes.json
        # records no return_model — the prints below surface size and a preview.

        # stylus-erc20 -> Any  (observed shape: str, Markdown code block)
        erc20 = await openzeppelin_stylus.stylus_erc20(caller, name="MyToken")
        print(f"stylus-erc20: {type(erc20).__name__} len={len(erc20)}")
        print(f"  head: {str(erc20)[:120]!r}")

        # stylus-erc721 -> Any  (observed shape: str, Markdown code block)
        erc721 = await openzeppelin_stylus.stylus_erc721(caller, name="MyToken")
        print(f"stylus-erc721: {type(erc721).__name__} len={len(erc721)}")
        print(f"  head: {str(erc721)[:120]!r}")

        # stylus-erc1155 -> Any  (observed shape: str, Markdown code block)
        erc1155 = await openzeppelin_stylus.stylus_erc1155(caller, name="MyToken")
        print(f"stylus-erc1155: {type(erc1155).__name__} len={len(erc1155)}")
        print(f"  head: {str(erc1155)[:120]!r}")


if __name__ == "__main__":
    asyncio.run(main())
