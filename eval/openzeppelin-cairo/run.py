"""
Smoke-test runner for generated openzeppelin-cairo/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/cairo/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-cairo/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "openzeppelin-cairo.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path as `openzeppelin_cairo`.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "openzeppelin-cairo.py")
_spec = importlib.util.spec_from_file_location("openzeppelin_cairo", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
openzeppelin_cairo = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_cairo"] = openzeppelin_cairo
_spec.loader.exec_module(openzeppelin_cairo)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/cairo/mcp"


def _preview(label: str, result: object) -> None:
    """Every tool here is annotated `-> Any`; observed shape is `str` (Cairo source)."""
    if isinstance(result, str):
        lines = result.splitlines()
        first = lines[0] if lines else ""
        print(f"{label}: str, {len(result)} chars, first line={first!r}")
    else:
        print(f"{label}: {type(result).__name__}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none. All 8 tools are pure contract-source
        # generators — they return Cairo text and write nothing server-side.
        # No tool in openzeppelin-cairo.shapes.json carries a discriminator,
        # so this is one call per tool.
        # Args are the real probed values from openzeppelin-cairo.verify.json.

        # cairo-custom -> Any  (minimal blank contract scaffold)
        custom = await openzeppelin_cairo.cairo_custom(caller, name="Registry")
        _preview("cairo-custom", custom)

        # cairo-erc20 -> Any
        erc20 = await openzeppelin_cairo.cairo_erc20(
            caller, name="MyToken", symbol="MTK"
        )
        _preview("cairo-erc20", erc20)

        # cairo-erc721 -> Any
        erc721 = await openzeppelin_cairo.cairo_erc721(
            caller, name="MyNFT", symbol="MNFT"
        )
        _preview("cairo-erc721", erc721)

        # cairo-erc1155 -> Any
        erc1155 = await openzeppelin_cairo.cairo_erc1155(
            caller,
            name="MyMultiToken",
            baseUri="https://example.com/metadata/{id}.json",
        )
        _preview("cairo-erc1155", erc1155)

        # cairo-account -> Any
        account = await openzeppelin_cairo.cairo_account(
            caller, name="MyAccount", type="stark"
        )
        _preview("cairo-account", account)

        # cairo-multisig -> Any
        multisig = await openzeppelin_cairo.cairo_multisig(
            caller, name="MyMultisig", quorum="2"
        )
        _preview("cairo-multisig", multisig)

        # cairo-vesting -> Any
        vesting = await openzeppelin_cairo.cairo_vesting(
            caller,
            name="MyVesting",
            startDate="2026-01-01T00:00",
            duration="365 days",
            cliffDuration="90 days",
            schedule="linear",
        )
        _preview("cairo-vesting", vesting)

        # cairo-governor -> Any  (largest payload of the eight)
        governor = await openzeppelin_cairo.cairo_governor(
            caller, name="MyGovernor", delay="1 day", period="1 week"
        )
        _preview("cairo-governor", governor)


if __name__ == "__main__":
    asyncio.run(main())
