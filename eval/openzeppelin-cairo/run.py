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
_spec = importlib.util.spec_from_file_location(
    "openzeppelin_cairo",
    os.path.join(os.path.dirname(__file__), "openzeppelin-cairo.py"),
)
openzeppelin_cairo = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_cairo"] = openzeppelin_cairo
_spec.loader.exec_module(openzeppelin_cairo)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/cairo/mcp"


def _preview(label: str, src) -> None:
    """Every tool here returns Any — in practice a Markdown code block (str)."""
    if isinstance(src, str):
        head = src.strip().splitlines()[0] if src.strip() else ""
        print(f"{label}: str, {len(src)} chars, first line {head!r}")
    else:
        print(f"{label}: {type(src).__name__}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none — every tool only renders Cairo source
        # into a Markdown code block and explicitly does not write to disk).
        # Args below are the real pre-scrub probe args from
        # openzeppelin-cairo.verify.json.

        # cairo_custom -> Any  (bare scaffold; simplest surface first)
        custom = await openzeppelin_cairo.cairo_custom(caller, name="MyContract")
        _preview("cairo_custom", custom)

        # cairo_erc20 -> Any
        erc20 = await openzeppelin_cairo.cairo_erc20(
            caller, name="MyToken", symbol="MTK", decimals="18", mintable=True
        )
        _preview("cairo_erc20", erc20)

        # cairo_erc721 -> Any
        erc721 = await openzeppelin_cairo.cairo_erc721(
            caller, name="MyNFT", symbol="MNFT", baseUri="https://example.com/nft/"
        )
        _preview("cairo_erc721", erc721)

        # cairo_erc1155 -> Any
        erc1155 = await openzeppelin_cairo.cairo_erc1155(
            caller,
            name="MyMultiToken",
            baseUri="https://example.com/token/{id}.json",
        )
        _preview("cairo_erc1155", erc1155)

        # cairo_account -> Any  (probed twice — one call per `type` variant)
        account_stark = await openzeppelin_cairo.cairo_account(
            caller, name="MyAccount", type="stark"
        )
        _preview("cairo_account(stark)", account_stark)

        account_eth = await openzeppelin_cairo.cairo_account(
            caller, name="MyAccount", type="eth"
        )
        _preview("cairo_account(eth)", account_eth)

        # cairo_multisig -> Any
        multisig = await openzeppelin_cairo.cairo_multisig(
            caller, name="MyMultisig", quorum="2"
        )
        _preview("cairo_multisig", multisig)

        # cairo_vesting -> Any
        vesting = await openzeppelin_cairo.cairo_vesting(
            caller,
            name="MyVesting",
            startDate="2026-03-15T14:30",
            duration="1 year",
            cliffDuration="90 days",
            schedule="linear",
        )
        _preview("cairo_vesting", vesting)

        # cairo_governor -> Any  (largest output; depends on a votes token above)
        governor = await openzeppelin_cairo.cairo_governor(
            caller,
            name="MyGovernor",
            delay="1 day",
            period="1 week",
            votes="erc20votes",
        )
        _preview("cairo_governor", governor)


if __name__ == "__main__":
    asyncio.run(main())
