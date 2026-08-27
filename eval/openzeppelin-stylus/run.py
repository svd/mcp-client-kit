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
# unimportable by a plain `import`, so load it by path under the name
# `openzeppelin_stylus`.
_spec = importlib.util.spec_from_file_location(
    "openzeppelin_stylus",
    os.path.join(os.path.dirname(__file__), "openzeppelin-stylus.py"),
)
assert _spec is not None and _spec.loader is not None
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
        # Skipped mutating tools: none — every tool on this server renders a
        # Rust/Stylus contract source string and mutates nothing server-side
        # ("Does not write to disk", per each tool description).
        # No shape entry carries a discriminator, so one call per tool.
        # Args come from openzeppelin-stylus.verify.json (real probed args);
        # the optional feature flags below are extras the schema allows.

        # stylus-erc20 -> Any  (observed: str — Rust source, ~2.5 KB)
        erc20 = await openzeppelin_stylus.stylus_erc20(
            caller, name="AlphaBeta"
        )
        print(f"stylus-erc20: {type(erc20).__name__} len={len(erc20)}")

        # stylus-erc721 -> Any  (observed: str — Rust source, ~2.3 KB)
        erc721 = await openzeppelin_stylus.stylus_erc721(caller, name="MyNft")
        print(f"stylus-erc721: {type(erc721).__name__} len={len(erc721)}")

        # stylus-erc1155 -> Any  (observed: str — Rust source, ~2.0 KB)
        erc1155 = await openzeppelin_stylus.stylus_erc1155(caller, name="MyMulti")
        print(f"stylus-erc1155: {type(erc1155).__name__} len={len(erc1155)}")


if __name__ == "__main__":
    asyncio.run(main())
