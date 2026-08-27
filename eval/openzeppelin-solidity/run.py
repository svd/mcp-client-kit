"""
Smoke-test runner for generated openzeppelin-solidity/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/solidity/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-solidity/run.py

Args come from openzeppelin-solidity.verify.json (real, pre-scrub probe args).
Every tool returns prose (Solidity source as a string), so there are no shaped
models to drill into — each block prints the response type and a short preview.
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# The wrapper module file is "openzeppelin-solidity.py"; the hyphen makes it
# un-importable by name, so load it directly from its path.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "openzeppelin-solidity.py")
_spec = importlib.util.spec_from_file_location("openzeppelin_solidity", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
oz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oz)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/solidity/mcp"


def _preview(label: str, result: object) -> None:
    """Every tool here returns Solidity source as prose -> report type + size."""
    if isinstance(result, str):
        first = result.strip().splitlines()[0] if result.strip() else ""
        print(f"{label}: str, {len(result)} chars, first line={first!r}")
    else:
        print(f"{label}: {type(result).__name__}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — all 8 tools are pure contract generators.
        # No discriminated tools in this server: one call per tool.

        # solidity-erc20 -> Any (prose)
        erc20 = await oz.solidity_erc20(
            caller,
            name="MyToken",
            symbol="MTK",
            premint="1000",
            mintable=True,
            burnable=True,
        )
        _preview("solidity-erc20", erc20)

        # solidity-erc721 -> Any (prose)
        erc721 = await oz.solidity_erc721(
            caller,
            name="MyNFT",
            symbol="MNFT",
            mintable=True,
            enumerable=True,
        )
        _preview("solidity-erc721", erc721)

        # solidity-erc1155 -> Any (prose)
        erc1155 = await oz.solidity_erc1155(
            caller,
            name="MyMulti",
            uri="https://example.com/token/{id}.json",
            mintable=True,
        )
        _preview("solidity-erc1155", erc1155)

        # solidity-stablecoin -> Any (prose)
        stablecoin = await oz.solidity_stablecoin(
            caller,
            name="MyStable",
            symbol="MUSD",
            decimals="6",
        )
        _preview("solidity-stablecoin", stablecoin)

        # solidity-rwa -> Any (prose)
        rwa = await oz.solidity_rwa(
            caller,
            name="MyAsset",
            symbol="MRWA",
            decimals="18",
        )
        _preview("solidity-rwa", rwa)

        # solidity-account -> Any (prose)
        account = await oz.solidity_account(
            caller,
            name="MyAccount",
            signatureValidation="ERC7739",
        )
        _preview("solidity-account", account)

        # solidity-governor -> Any (prose)
        governor = await oz.solidity_governor(
            caller,
            name="MyGovernor",
            delay="1 day",
            period="1 week",
        )
        _preview("solidity-governor", governor)

        # solidity-custom -> Any (prose)
        custom = await oz.solidity_custom(
            caller,
            name="GammaToken",
            upgradeable="uups",
        )
        _preview("solidity-custom", custom)


if __name__ == "__main__":
    asyncio.run(main())
