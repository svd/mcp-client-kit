"""
Smoke-test runner for generated openzeppelin-stylus/ wrappers.
Transport: Streamable HTTP  (https://mcp.openzeppelin.com/contracts/stylus/mcp)
Auth: none (public endpoint)

Usage:
    python eval/openzeppelin-stylus/run.py

Args come from openzeppelin-stylus.verify.json (real, pre-scrub probe args).

Note: the generated wrapper module lives at `openzeppelin-stylus.py`, whose
filename contains a hyphen and so cannot be imported with a plain `import`
statement. We load it directly from its file path under a valid identifier to
avoid a SyntaxError/ModuleNotFoundError.
"""
import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "openzeppelin-stylus.py"
)
_spec = importlib.util.spec_from_file_location("openzeppelin_stylus", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
openzeppelin_stylus = importlib.util.module_from_spec(_spec)
sys.modules["openzeppelin_stylus"] = openzeppelin_stylus
_spec.loader.exec_module(openzeppelin_stylus)

from mcpgen import McpBridgeCaller

SERVER_URL = "https://mcp.openzeppelin.com/contracts/stylus/mcp"


def _preview(label: str, source: object) -> None:
    """Every tool returns `Any` — in practice a Markdown code block of Rust source.

    shapes.json records `_observed_shape: "str"` with no `return_model` and no
    unwrap for all three tools, so there is nothing to drill into: report the
    type, the size, and the first non-empty line.
    """
    if isinstance(source, str):
        first = next((ln for ln in source.splitlines() if ln.strip()), "")
        print(f"{label}: str  {len(source)} chars  first_line={first!r}")
    else:
        print(f"{label}: {type(source).__name__}")


async def main() -> None:
    caller = McpBridgeCaller(url=SERVER_URL)

    # One connection for the whole run: a single initialize() instead of
    # reconnecting for every tool call.
    async with caller.connected():
        # Skipped mutating tools: none — all three tools generate contract
        # source and return it as text ("Does not write to disk").
        # No discriminated tools in shapes.json, so one call per tool.

        # stylus-erc20 -> Any  (fungible token)
        erc20 = await openzeppelin_stylus.stylus_erc20(caller, name="GammaAsset")
        _preview("stylus-erc20", erc20)

        # stylus-erc721 -> Any  (non-fungible token)
        erc721 = await openzeppelin_stylus.stylus_erc721(caller, name="AlphaNFT")
        _preview("stylus-erc721", erc721)

        # stylus-erc1155 -> Any  (multi-token)
        erc1155 = await openzeppelin_stylus.stylus_erc1155(caller, name="AlphaMulti")
        _preview("stylus-erc1155", erc1155)


if __name__ == "__main__":
    asyncio.run(main())
