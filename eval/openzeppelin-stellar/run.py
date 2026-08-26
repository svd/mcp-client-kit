"""
Smoke-test runner for generated openzeppelin-stellar/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/stellar/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-stellar/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "openzeppelin-stellar.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path under the name
# `openzeppelin_stellar`.
_spec = importlib.util.spec_from_file_location(
    "openzeppelin_stellar",
    os.path.join(os.path.dirname(__file__), "openzeppelin-stellar.py"),
)
openzeppelin_stellar = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_stellar"] = openzeppelin_stellar
_spec.loader.exec_module(openzeppelin_stellar)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/stellar/mcp"


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — every tool on this server renders a
        # Rust/Soroban contract source string and mutates nothing server-side.
        # No shape entry carries a discriminator, so one call per tool.
        # Args come from openzeppelin-stellar.verify.json (real probed args).

        # stellar-fungible -> Any  (observed: str — Rust source)
        fungible = await openzeppelin_stellar.stellar_fungible(
            caller, name="MyToken", symbol="MTK"
        )
        print(f"stellar-fungible: {type(fungible).__name__} len={len(fungible)}")

        # stellar-non-fungible -> Any  (observed: str — Rust source)
        non_fungible = await openzeppelin_stellar.stellar_non_fungible(
            caller, name="MyNFT", symbol="MNFT"
        )
        print(
            f"stellar-non-fungible: {type(non_fungible).__name__} "
            f"len={len(non_fungible)}"
        )

        # stellar-stablecoin -> Any  (observed: str — Rust source)
        stablecoin = await openzeppelin_stellar.stellar_stablecoin(
            caller, name="MyStablecoin", symbol="MSC"
        )
        print(f"stellar-stablecoin: {type(stablecoin).__name__} len={len(stablecoin)}")

        # stellar-vault -> Any  (observed: str — Rust source)
        vault = await openzeppelin_stellar.stellar_vault(
            caller, name="MyVault", symbol="MVLT"
        )
        print(f"stellar-vault: {type(vault).__name__} len={len(vault)}")

        # stellar-account -> Any  (observed: str — Rust source)
        account = await openzeppelin_stellar.stellar_account(caller, name="MyAccount")
        print(f"stellar-account: {type(account).__name__} len={len(account)}")

        # stellar-governor -> Any  (observed: str — Rust source)
        governor = await openzeppelin_stellar.stellar_governor(
            caller, name="MyGovernor"
        )
        print(f"stellar-governor: {type(governor).__name__} len={len(governor)}")


if __name__ == "__main__":
    asyncio.run(main())
