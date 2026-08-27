"""
Smoke-test runner for generated openzeppelin-stellar/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/stellar/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-stellar/run.py

Args come from openzeppelin-stellar.verify.json (real, pre-scrub probe args).

Every tool on this server is a pure Rust/Soroban source-code generator: it takes
a contract configuration and returns the rendered contract source as a plain
string in a Markdown code block. Nothing is written to disk, so all six tools are
read-only and none carry a discriminator - one call per tool, previewing the
returned source.

Note: the generated wrapper module lives at `openzeppelin-stellar.py`, whose
filename contains a hyphen and so cannot be imported with a plain `import`
statement. We load it directly from its file path under a valid identifier to
avoid a SyntaxError/ModuleNotFoundError.
"""
import asyncio
import importlib.util
import os

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "openzeppelin-stellar.py"
)
_spec = importlib.util.spec_from_file_location(
    "openzeppelin_stellar_wrappers", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
openzeppelin_stellar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(openzeppelin_stellar)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/stellar/mcp"


def _summarize(source: object, label: str) -> None:
    """Print size + first meaningful line of a generated contract source string."""
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
        # read-only source-code generator that does not write to disk)

        # stellar-account -> Any  (smart account with configurable signers)
        account = await openzeppelin_stellar.stellar_account(
            caller,
            name="MyAccount",
        )
        _summarize(account, "stellar-account")

        # stellar-fungible -> Any  (SEP-41 / ERC-20-like token)
        fungible = await openzeppelin_stellar.stellar_fungible(
            caller,
            name="MyToken",
            symbol="MTK",
        )
        _summarize(fungible, "stellar-fungible")

        # stellar-non-fungible -> Any  (SEP-50 / ERC-721-like token)
        non_fungible = await openzeppelin_stellar.stellar_non_fungible(
            caller,
            name="MyNFT",
            symbol="MNFT",
        )
        _summarize(non_fungible, "stellar-non-fungible")

        # stellar-stablecoin -> Any  (fungible token specialised as a stablecoin)
        stablecoin = await openzeppelin_stellar.stellar_stablecoin(
            caller,
            name="MyStablecoin",
            symbol="MSC",
        )
        _summarize(stablecoin, "stellar-stablecoin")

        # stellar-vault -> Any  (ERC-4626-like tokenized vault over an asset)
        vault = await openzeppelin_stellar.stellar_vault(
            caller,
            name="MyVault",
            symbol="MVLT",
        )
        _summarize(vault, "stellar-vault")

        # stellar-governor -> Any  (largest payload of the six)
        governor = await openzeppelin_stellar.stellar_governor(
            caller,
            name="MyGovernor",
        )
        _summarize(governor, "stellar-governor")


if __name__ == "__main__":
    asyncio.run(main())
