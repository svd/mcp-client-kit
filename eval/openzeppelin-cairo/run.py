"""
Smoke-test runner for generated openzeppelin-cairo/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/cairo/mcp)
Auth: none (public endpoint)

Args come from openzeppelin-cairo.verify.json (real, pre-scrub probe args).
Every tool returns Any — the server answers with a Markdown code block holding
Cairo source, so each block prints the payload type and size instead of drilling
into fields. No tool declares a discriminator, so each is called once.

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
assert _spec is not None and _spec.loader is not None
openzeppelin_cairo = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_cairo"] = openzeppelin_cairo
_spec.loader.exec_module(openzeppelin_cairo)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/cairo/mcp"


def _summarize(label: str, value: object) -> None:
    """Every tool returns Any; report the payload type and size."""
    if isinstance(value, str):
        print(f"{label}: str, {len(value)} chars")
    else:
        print(f"{label}: {type(value).__name__}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — all eight tools render source code
        # and, per their descriptions, do not write to disk.

        # cairo-custom -> Any  (blank scaffold; simplest shape first)
        custom = await openzeppelin_cairo.cairo_custom(caller, name="MyContract")
        _summarize("cairo-custom", custom)

        # cairo-account -> Any
        account = await openzeppelin_cairo.cairo_account(
            caller, name="MyAccount", type="stark"
        )
        _summarize("cairo-account", account)

        # cairo-erc20 -> Any
        erc20 = await openzeppelin_cairo.cairo_erc20(
            caller, name="MyToken", symbol="MTK", decimals="18"
        )
        _summarize("cairo-erc20", erc20)

        # cairo-erc721 -> Any
        erc721 = await openzeppelin_cairo.cairo_erc721(
            caller, name="MyNFT", symbol="MNFT"
        )
        _summarize("cairo-erc721", erc721)

        # cairo-erc1155 -> Any
        erc1155 = await openzeppelin_cairo.cairo_erc1155(
            caller, name="MyMultiToken", baseUri="https://example.com/tokens/"
        )
        _summarize("cairo-erc1155", erc1155)

        # cairo-governor -> Any  (governance over the token standards above)
        governor = await openzeppelin_cairo.cairo_governor(
            caller, name="MyGovernor", delay="1 day", period="1 week"
        )
        _summarize("cairo-governor", governor)

        # cairo-multisig -> Any
        multisig = await openzeppelin_cairo.cairo_multisig(
            caller, name="MyMultisig", quorum="2"
        )
        _summarize("cairo-multisig", multisig)

        # cairo-vesting -> Any
        vesting = await openzeppelin_cairo.cairo_vesting(
            caller,
            name="MyVesting",
            startDate="2026-01-01T00:00:00",
            duration="90 day",
            cliffDuration="30 day",
            schedule="linear",
        )
        _summarize("cairo-vesting", vesting)


if __name__ == "__main__":
    asyncio.run(main())
