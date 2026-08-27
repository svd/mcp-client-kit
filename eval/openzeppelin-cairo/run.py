"""
Smoke-test runner for generated openzeppelin-cairo/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/cairo/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-cairo/run.py

Args come from openzeppelin-cairo.verify.json (real, pre-scrub probe args).

Every tool on this server is a pure Cairo source-code generator: it takes a
contract configuration and returns the rendered `.cairo` source as a plain
string. Nothing is written anywhere, so all eight tools are read-only and none
carry a discriminator - one call per tool, previewing the returned source.

Note: the generated wrapper module lives at `openzeppelin-cairo.py`, whose
filename contains a hyphen and so cannot be imported with a plain `import`
statement. We load it directly from its file path under a valid identifier to
avoid a SyntaxError/ModuleNotFoundError.
"""
import asyncio
import importlib.util
import os

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "openzeppelin-cairo.py"
)
_spec = importlib.util.spec_from_file_location(
    "openzeppelin_cairo_wrappers", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
openzeppelin_cairo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(openzeppelin_cairo)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/cairo/mcp"


def _summarize(source: object, label: str) -> None:
    """Print size + first meaningful line of a generated Cairo source string."""
    text = "" if source is None else str(source)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = lines[0] if lines else ""
    if len(head) > 72:
        head = head[:72] + "..."
    print(f"{label}: {type(source).__name__} {len(text)} char(s), {len(lines)} line(s)")
    print(f"  first line: {head!r}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: (none - every tool on this server is a
        # read-only source-code generator)

        # cairo-custom -> Any  (bare scaffold; simplest contract shape)
        custom = await openzeppelin_cairo.cairo_custom(caller, name="MyContract")
        _summarize(custom, "cairo-custom")

        # cairo-account -> Any  (type is a Literal['stark', 'eth'] discriminator
        # on the input side only; the return shape is a source string either way,
        # and only 'stark' was probed)
        account = await openzeppelin_cairo.cairo_account(
            caller,
            name="MyAccount",
            type="stark",
        )
        _summarize(account, "cairo-account(stark)")

        # cairo-erc20 -> Any
        erc20 = await openzeppelin_cairo.cairo_erc20(
            caller,
            name="MyToken",
            symbol="MTK",
        )
        _summarize(erc20, "cairo-erc20")

        # cairo-erc721 -> Any
        erc721 = await openzeppelin_cairo.cairo_erc721(
            caller,
            name="MyNFT",
            symbol="MNFT",
        )
        _summarize(erc721, "cairo-erc721")

        # cairo-erc1155 -> Any
        erc1155 = await openzeppelin_cairo.cairo_erc1155(
            caller,
            name="MyMultiToken",
            baseUri="https://example.com/metadata/{id}.json",
        )
        _summarize(erc1155, "cairo-erc1155")

        # cairo-governor -> Any  (largest payload of the eight)
        governor = await openzeppelin_cairo.cairo_governor(
            caller,
            name="MyGovernor",
            delay="1 day",
            period="1 week",
        )
        _summarize(governor, "cairo-governor")

        # cairo-multisig -> Any
        multisig = await openzeppelin_cairo.cairo_multisig(
            caller,
            name="MyMultisig",
            quorum="2",
        )
        _summarize(multisig, "cairo-multisig")

        # cairo-vesting -> Any
        vesting = await openzeppelin_cairo.cairo_vesting(
            caller,
            name="VestingWallet",
            startDate="2026-03-15T14:30",
            duration="1 year",
            cliffDuration="90 days",
            schedule="linear",
        )
        _summarize(vesting, "cairo-vesting")


if __name__ == "__main__":
    asyncio.run(main())
