"""
Smoke-test runner for generated openzeppelin-solidity/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/solidity/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-solidity/run.py
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
# The wrapper module file is "openzeppelin-solidity.py" — the hyphen makes it
# unimportable by a plain `import`, so load it by path under the name
# `openzeppelin_solidity`.
_MODULE_PATH = os.path.join(os.path.dirname(__file__), "openzeppelin-solidity.py")
_spec = importlib.util.spec_from_file_location("openzeppelin_solidity", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
openzeppelin_solidity = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_solidity"] = openzeppelin_solidity
_spec.loader.exec_module(openzeppelin_solidity)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/solidity/mcp"


def _preview(label: str, result: object) -> None:
    """Every tool here returns `Any` (observed shape: str — Solidity source)."""
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
        # Skipped mutating tools: none — all 8 tools are pure contract-source
        # generators; nothing is written server-side.
        # No discriminated tools in this server's shape spec, so one call per tool.
        # Args are the real probed values from openzeppelin-solidity.verify.json.

        # solidity-custom -> Any  (minimal blank contract scaffold)
        custom = await openzeppelin_solidity.solidity_custom(caller, name="EvalCustom")
        _preview("solidity-custom", custom)

        # solidity-erc20 -> Any
        erc20 = await openzeppelin_solidity.solidity_erc20(
            caller, name="EvalToken", symbol="EVT", decimals="18"
        )
        _preview("solidity-erc20", erc20)

        # solidity-erc721 -> Any
        erc721 = await openzeppelin_solidity.solidity_erc721(
            caller, name="EvalNFT", symbol="ENFT"
        )
        _preview("solidity-erc721", erc721)

        # solidity-erc1155 -> Any
        erc1155 = await openzeppelin_solidity.solidity_erc1155(
            caller, name="EvalMulti", uri="https://example.com/{id}.json"
        )
        _preview("solidity-erc1155", erc1155)

        # solidity-stablecoin -> Any
        stablecoin = await openzeppelin_solidity.solidity_stablecoin(
            caller, name="EvalStable", symbol="EUSD"
        )
        _preview("solidity-stablecoin", stablecoin)

        # solidity-rwa -> Any
        rwa = await openzeppelin_solidity.solidity_rwa(
            caller, name="EvalRWA", symbol="ERWA"
        )
        _preview("solidity-rwa", rwa)

        # solidity-account -> Any
        account = await openzeppelin_solidity.solidity_account(caller, name="EvalAccount")
        _preview("solidity-account", account)

        # solidity-governor -> Any  (largest output; `decimals` is int-coerced
        # by the shape spec's input_overrides)
        governor = await openzeppelin_solidity.solidity_governor(
            caller, name="EvalGov", delay="1 day", period="1 week"
        )
        _preview("solidity-governor", governor)


if __name__ == "__main__":
    asyncio.run(main())
