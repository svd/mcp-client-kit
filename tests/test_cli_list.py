"""Tests for `mcpgen list` — network-free; _list_tools is monkeypatched."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import pytest
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from mcpgen import _bridge
from mcpgen.cli import Inventory, _cmd_list, _list_inventory, _list_tools, main

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup, ExceptionGroup

# ── helpers ───────────────────────────────────────────────────────────────────


def _ns(server: str, schema: bool = False, **extra) -> SimpleNamespace:
    return SimpleNamespace(
        server=server,
        schema=schema,
        **extra,
        stdio=None,
        url=None,
        bearer=None,
        client_name=None,
        config=None,
        cred_backend=None,
        env=None,
    )


FAKE_TOOLS = [
    {
        "name": "get_user",
        "description": "Get user by ID",
        "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"]},
    },
    {
        "name": "list_users",
        "description": "List all users",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
]


# ── without --schema ──────────────────────────────────────────────────────────


def test_list_without_schema_includes_name_and_description(capsys):
    """Without --schema, output is {name, description} only."""
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        rc = _cmd_list(ns)

    assert rc == 0
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result) == 2
    assert result[0] == {"name": "get_user", "description": "Get user by ID"}
    assert result[1] == {"name": "list_users", "description": "List all users"}


def test_list_without_schema_no_inputschema_in_output(capsys):
    """Without --schema, inputSchema should not be in the output."""
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    for item in result:
        assert "inputSchema" not in item


def test_list_without_schema_empty_description_fallback(capsys):
    """When description is None or absent, it should fallback to empty string."""
    tools = [
        {"name": "no_desc_none", "description": None, "inputSchema": {}},
        {"name": "no_desc_absent", "inputSchema": {}},
    ]
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0] == {"name": "no_desc_none", "description": ""}
    assert result[1] == {"name": "no_desc_absent", "description": ""}


# ── with --schema ─────────────────────────────────────────────────────────────


def test_list_with_schema_includes_inputschema(capsys):
    """With --schema, output includes inputSchema."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        rc = _cmd_list(ns)

    assert rc == 0
    out = capsys.readouterr().out
    result = json.loads(out)

    assert len(result) == 2
    assert "inputSchema" in result[0]
    assert result[0]["inputSchema"] == FAKE_TOOLS[0]["inputSchema"]
    assert result[1]["inputSchema"] == FAKE_TOOLS[1]["inputSchema"]


def test_list_with_schema_includes_name_and_description(capsys):
    """With --schema, output still includes name and description."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["name"] == "get_user"
    assert result[0]["description"] == "Get user by ID"
    assert result[1]["name"] == "list_users"
    assert result[1]["description"] == "List all users"


def test_list_with_schema_empty_inputschema_fallback(capsys):
    """When inputSchema is None or absent, it should fallback to empty dict."""
    tools = [
        {"name": "no_schema_none", "description": "A tool", "inputSchema": None},
        {"name": "no_schema_absent", "description": "A tool"},
    ]
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["inputSchema"] == {}
    assert result[1]["inputSchema"] == {}


def test_list_with_schema_all_fields_present(capsys):
    """With --schema, each tool has all three fields: name, description, inputSchema."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    for item in result:
        assert set(item.keys()) == {"name", "description", "inputSchema"}


# ── annotations / readOnlyHint ────────────────────────────────────────────────


def _mock_tool(name: str, description: str, annotations=None):
    t = MagicMock()
    t.name = name
    t.description = description
    t.inputSchema = {}
    t.annotations = annotations
    return t


def test_list_tools_surfaces_readonly_hint_annotation():
    """_list_tools must not drop the MCP `annotations` object (readOnlyHint lives there)."""
    annotations = MagicMock()
    annotations.model_dump.return_value = {"readOnlyHint": True}
    tool = _mock_tool("whoami", "Return caller identity", annotations=annotations)

    mock_session = MagicMock()
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield mock_session

    with patch("mcpgen.cli._bridge.session", fake_session):
        result = asyncio.run(_list_tools("acme"))

    assert result[0]["annotations"] == {"readOnlyHint": True}


def test_list_tools_annotations_absent_is_none():
    """A tool with no annotations object surfaces annotations: None (not dropped)."""
    tool = _mock_tool("get_user", "Get user", annotations=None)

    mock_session = MagicMock()
    mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))

    @asynccontextmanager
    async def fake_session(server, **kwargs):
        yield mock_session

    with patch("mcpgen.cli._bridge.session", fake_session):
        result = asyncio.run(_list_tools("acme"))

    assert result[0]["annotations"] is None


def test_list_with_schema_surfaces_readonly_hint(capsys):
    """`mcpgen list --schema` output includes annotations when _list_tools provides them."""
    tools = [
        {
            "name": "delete_repo",
            "description": "Delete a repo",
            "inputSchema": {},
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "get_repo",
            "description": "Get a repo",
            "inputSchema": {},
            "annotations": {"readOnlyHint": True},
        },
    ]
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["annotations"] == {"readOnlyHint": False}
    assert result[1]["annotations"] == {"readOnlyHint": True}


def test_list_without_schema_surfaces_readonly_hint(capsys):
    """`mcpgen list` (no --schema) still surfaces annotations when present."""
    tools = [
        {"name": "delete_repo", "description": "Delete a repo", "annotations": {"readOnlyHint": False}},
    ]
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=tools):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    assert result[0]["annotations"] == {"readOnlyHint": False}


def test_list_annotations_omitted_when_absent(capsys):
    """Tools with no annotations key must not gain one in the printed output
    (keeps the existing {name, description[, inputSchema]} contract intact)."""
    ns = _ns("acme", schema=True)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        _cmd_list(ns)

    out = capsys.readouterr().out
    result = json.loads(out)

    for item in result:
        assert "annotations" not in item


# ── error handling ────────────────────────────────────────────────────────────


def test_list_handles_list_tools_error(capsys):
    """When _list_tools raises an error, _cmd_list should return 1."""
    ns = _ns("acme", schema=False)

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=FileNotFoundError("server not found")):
        rc = _cmd_list(ns)

    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_list_grouped_tools_error_exits_one_without_traceback(capsys):
    """Real transports deliver a tools/list McpError inside an ExceptionGroup."""
    group = ExceptionGroup("x", [McpError(mcp_types.ErrorData(code=-32603, message="boom"))])

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, side_effect=group):
        rc = _cmd_list(_ns("acme", schema=False))

    assert rc == 1
    assert capsys.readouterr().err == "[list] error: McpError: boom\n"


# ── inventory plumbing (_list_tools hook, _list_inventory) ────────────────────


class _FakeSessions:
    """A `_bridge.session` stand-in that counts opens and tracks whether one is live.

    Like the real transports, it runs the session body inside an anyio task group,
    so an exception escaping the body arrives wrapped in an ExceptionGroup.
    `kill(exc)` fails the session from a sibling task, as a transport's request
    task does on an HTTP 502: the body sees only a cancellation. `close_with` is
    raised once the task group has exited, as a failing session close.
    """

    def __init__(self, tools=(), capabilities=None):
        self.opened = 0
        self.live = False
        self.close_with: BaseException | None = None
        self.session = MagicMock()
        self.session.list_tools = AsyncMock(return_value=MagicMock(tools=list(tools)))
        self.session.get_server_capabilities = MagicMock(return_value=capabilities)
        self._tg = None

    @asynccontextmanager
    async def __call__(self, server, **kwargs):
        self.opened += 1
        self.live = True
        try:
            async with anyio.create_task_group() as tg:
                self._tg = tg
                yield self.session
            if self.close_with is not None:
                raise self.close_with
        finally:
            self.live = False

    def kill(self, exc: BaseException) -> None:
        async def request_task():
            raise exc

        self._tg.start_soon(request_task)

    def killing(self, exc: BaseException):
        """A resource lister/preview whose request fails the session from a sibling task."""

        async def lister(s, **kwargs):
            self.kill(exc)
            await anyio.sleep_forever()

        return lister


RESOURCES = [{"uri": "risk://dictionary", "name": "risk_dictionary", "mimeType": "application/json"}]
TEMPLATES = [{"uriTemplate": "dimension://by-unit-type/{unitType}", "name": "by_unit_type"}]


def test_list_tools_awaits_hook_inside_session_after_listing():
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])
    seen = []

    async def hook(s):
        seen.append((s, fake.live, fake.session.list_tools.await_count))

    with patch("mcpgen.cli._bridge.session", fake):
        tools = asyncio.run(_list_tools("acme", on_session=hook))

    assert [t["name"] for t in tools] == ["whoami"]
    assert seen == [(fake.session, True, 1)]


def test_list_inventory_collects_everything_in_one_session():
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])
    lister = AsyncMock(return_value=(RESOURCES, TEMPLATES))

    with patch("mcpgen.cli._bridge.session", fake), patch("mcpgen.cli._bridge.list_resource_inventory", lister):
        inv = asyncio.run(_list_inventory("acme", scope="all"))

    assert fake.opened == 1
    lister.assert_awaited_once_with(fake.session, statics=True)
    assert [t["name"] for t in inv.tools] == ["whoami"]
    assert inv.resources == RESOURCES
    assert inv.templates == TEMPLATES


def test_list_inventory_forwards_connection_args():
    fake_list_tools = AsyncMock(return_value=[])

    with patch("mcpgen.cli._list_tools", fake_list_tools):
        asyncio.run(_list_inventory("acme", scope="all", cmd="python srv.py", bearer="t0k"))

    _, kwargs = fake_list_tools.call_args
    assert kwargs["cmd"] == "python srv.py"
    assert kwargs["bearer"] == "t0k"
    assert kwargs["on_session"] is not None


def test_list_inventory_scope_none_skips_hook():
    fake_list_tools = AsyncMock(return_value=FAKE_TOOLS)
    lister = AsyncMock()

    with (
        patch("mcpgen.cli._list_tools", fake_list_tools),
        patch("mcpgen.cli._bridge.list_resource_inventory", lister),
    ):
        inv = asyncio.run(_list_inventory("acme", scope="none"))

    assert fake_list_tools.call_args.kwargs["on_session"] is None
    lister.assert_not_awaited()
    assert inv == Inventory(tools=FAKE_TOOLS, resources=None, templates=None)


def test_list_inventory_scope_templates_leaves_statics_uncollected():
    fake = _FakeSessions()
    lister = AsyncMock(return_value=([], TEMPLATES))

    with (
        patch("mcpgen.cli._bridge.session", fake),
        patch("mcpgen.cli._bridge.list_resource_inventory", lister),
    ):
        inv = asyncio.run(_list_inventory("acme", scope="templates"))

    lister.assert_awaited_once_with(fake.session, statics=False)
    assert inv.resources is None
    assert inv.templates == TEMPLATES


def test_list_inventory_no_capability_is_tracked_empty():
    fake = _FakeSessions(capabilities=mcp_types.ServerCapabilities())

    with patch("mcpgen.cli._bridge.session", fake):
        inv = asyncio.run(_list_inventory("acme", scope="all"))

    assert inv.resources == []
    assert inv.templates == []


def test_list_inventory_with_patched_list_tools_is_untracked():
    """The existing `patch("mcpgen.cli._list_tools")` sites never run the hook."""
    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock, return_value=FAKE_TOOLS):
        inv = asyncio.run(_list_inventory("acme", scope="all"))

    assert inv == Inventory(tools=FAKE_TOOLS, resources=None, templates=None)


def test_list_inventory_rejects_unknown_scope():
    with pytest.raises(ValueError, match="scope"):
        asyncio.run(_list_inventory("acme", scope="some"))


@pytest.mark.parametrize(
    "exc",
    [McpError(mcp_types.ErrorData(code=-32603, message="boom")), ValueError("repeated cursor")],
)
def test_list_inventory_raises_listing_error_unwrapped(exc):
    """A resource-listing failure escapes the session as itself, not as an ExceptionGroup."""
    fake = _FakeSessions()

    with (
        patch("mcpgen.cli._bridge.session", fake),
        patch("mcpgen.cli._bridge.list_resource_inventory", AsyncMock(side_effect=exc)),
        pytest.raises(type(exc)) as caught,
    ):
        asyncio.run(_list_inventory("acme", scope="all"))

    assert caught.value is exc


def test_list_tools_hook_failure_propagates():
    fake = _FakeSessions()

    async def hook(s):
        raise RuntimeError("hook exploded")

    with patch("mcpgen.cli._bridge.session", fake), pytest.raises(ExceptionGroup) as caught:
        asyncio.run(_list_tools("acme", on_session=hook))

    assert [type(e) for e in caught.value.exceptions] == [RuntimeError]


def test_list_inventory_non_strict_keeps_tools_and_records_error():
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])
    exc = McpError(mcp_types.ErrorData(code=-32603, message="boom"))

    with (
        patch("mcpgen.cli._bridge.session", fake),
        patch("mcpgen.cli._bridge.preview_resource_inventory", AsyncMock(side_effect=exc)),
    ):
        inv = asyncio.run(_list_inventory("acme", scope="all", strict=False))

    assert [t["name"] for t in inv.tools] == ["whoami"]
    assert inv.resources is None
    assert inv.templates is None
    assert inv.resource_error is exc


# ── list --resources ──────────────────────────────────────────────────────────

LISTED_RESOURCES = [
    {
        "uri": "risk://dictionary",
        "name": "risk_dictionary",
        "title": "Risk dictionary",
        "description": "Read this before calling upsert_unit_risk",
        "mimeType": "application/json",
        "size": 2048,
        "icons": [{"src": "data:image/png;base64," + "A" * 64}],
        "annotations": {"audience": ["assistant"]},
        "_meta": {"vendor": "x"},
        "vendorExtra": 1,
    },
    {"uri": "dimension://all", "name": "dimensions"},
]
LISTED_TEMPLATES = [
    {
        "uriTemplate": "dimension://by-unit-type/{unitType}",
        "name": "by_unit_type",
        "description": "Dimensions for one unit type",
        "mimeType": "application/json",
        "icons": [{"src": "https://example.com/i.png"}],
        "_meta": {"vendor": "x"},
    }
]


def _run_list(ns_or_argv, *, lister=None, preview=None, tools=(), capabilities=None, fake=None):
    """Run `list` over a fake session (*fake*, or a new one from *tools*/*capabilities*).

    *lister* replaces the bridge's full resource listing (`--resources`), *preview*
    its first-page preview (plain `list`); either left None runs the real one.
    """
    fake = fake or _FakeSessions(tools=tools, capabilities=capabilities)
    run = main if isinstance(ns_or_argv, list) else _cmd_list
    with ExitStack() as stack:
        stack.enter_context(patch("mcpgen.cli._bridge.session", fake))
        if lister is not None:
            stack.enter_context(patch("mcpgen.cli._bridge.list_resource_inventory", lister))
        if preview is not None:
            stack.enter_context(patch("mcpgen.cli._bridge.preview_resource_inventory", preview))
        return run(ns_or_argv), fake


def test_list_resources_prints_resource_inventory_json(capsys):
    lister = AsyncMock(return_value=(LISTED_RESOURCES, LISTED_TEMPLATES))

    rc, fake = _run_list(_ns("acme", resources=True), lister=lister, tools=[_mock_tool("whoami", "Who")])

    assert rc == 0
    assert fake.opened == 1
    assert json.loads(capsys.readouterr().out) == {
        "resources": [
            {
                "uri": "risk://dictionary",
                "name": "risk_dictionary",
                "title": "Risk dictionary",
                "description": "Read this before calling upsert_unit_risk",
                "mimeType": "application/json",
                "size": 2048,
            },
            {"uri": "dimension://all", "name": "dimensions"},
        ],
        "resourceTemplates": [
            {
                "uriTemplate": "dimension://by-unit-type/{unitType}",
                "name": "by_unit_type",
                "description": "Dimensions for one unit type",
                "mimeType": "application/json",
            }
        ],
    }


def test_list_resources_without_capability_prints_empty_lists(capsys):
    rc, fake = _run_list(
        _ns("acme", resources=True),
        capabilities=mcp_types.ServerCapabilities(),
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"resources": [], "resourceTemplates": []}
    fake.session.list_resources.assert_not_called()


def test_list_default_output_unchanged_plus_hint(capsys):
    tools = [_mock_tool("whoami", "Who am I"), _mock_tool("get_user", "Get user")]
    preview = AsyncMock(return_value=(LISTED_RESOURCES, False, LISTED_TEMPLATES, False))

    with patch("mcpgen.cli._list_tools", new_callable=AsyncMock) as baseline:
        baseline.return_value = [
            {"name": "whoami", "description": "Who am I", "inputSchema": {}, "annotations": None},
            {"name": "get_user", "description": "Get user", "inputSchema": {}, "annotations": None},
        ]
        _cmd_list(_ns("acme"))
    expected = capsys.readouterr()

    rc, fake = _run_list(_ns("acme"), preview=preview, tools=tools)

    assert rc == 0
    assert fake.opened == 1
    preview.assert_awaited_once_with(fake.session, statics=True)
    got = capsys.readouterr()
    assert got.out == expected.out
    assert expected.err == ""
    assert got.err == (
        "[list] 2 resource(s) and 1 resource template(s) available; re-run with `--resources` to list them\n"
    )


def test_list_default_hint_marks_a_truncated_first_page(capsys):
    """Plain `list` reads one page per list; a further page shows as "N+"."""
    fake = _FakeSessions(
        tools=[_mock_tool("whoami", "Who am I")],
        capabilities=mcp_types.ServerCapabilities(resources=mcp_types.ResourcesCapability()),
    )
    fake.session.list_resources = AsyncMock(
        return_value=mcp_types.ListResourcesResult(
            resources=[mcp_types.Resource(uri="demo://a", name="a"), mcp_types.Resource(uri="demo://b", name="b")],
            nextCursor="page-2",
        )
    )
    fake.session.list_resource_templates = AsyncMock(
        return_value=mcp_types.ListResourceTemplatesResult(
            resourceTemplates=[mcp_types.ResourceTemplate(uriTemplate="demo://t/{x}", name="t")]
        )
    )

    with patch("mcpgen.cli._bridge.session", fake):
        rc = _cmd_list(_ns("acme"))

    assert rc == 0
    fake.session.list_resources.assert_awaited_once_with(params=None)
    assert capsys.readouterr().err == (
        "[list] 2+ resource(s) and 1 resource template(s) available; re-run with `--resources` to list them\n"
    )


def test_list_default_without_capability_has_no_hint(capsys):
    rc, _ = _run_list(
        _ns("acme"),
        tools=[_mock_tool("whoami", "Who am I")],
        capabilities=mcp_types.ServerCapabilities(),
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"name": "whoami", "description": "Who am I"}]
    assert captured.err == ""


@pytest.mark.parametrize(
    "exc",
    [McpError(mcp_types.ErrorData(code=-32603, message="resources exploded")), ValueError("resources exploded")],
)
def test_list_resources_listing_error_exits_1_without_traceback(capsys, exc):
    rc, _ = _run_list(_ns("acme", resources=True), lister=AsyncMock(side_effect=exc))

    assert rc == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[list] error: " in captured.err
    assert "resources exploded" in captured.err
    assert "Traceback" not in captured.err


def test_list_default_survives_resource_listing_error(capsys):
    exc = McpError(mcp_types.ErrorData(code=-32603, message="resources exploded"))

    rc, fake = _run_list(_ns("acme"), preview=AsyncMock(side_effect=exc), tools=[_mock_tool("whoami", "Who am I")])

    assert rc == 0
    assert fake.opened == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"name": "whoami", "description": "Who am I"}]
    assert captured.err == "[list] note: could not list resources: McpError: resources exploded\n"


def _http_502() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://acme.example.com/mcp")
    return httpx.HTTPStatusError(
        "Server error '502 Bad Gateway'", request=request, response=httpx.Response(502, request=request)
    )


TRANSPORT_FAULTS = pytest.mark.parametrize(
    "make_exc", [lambda: RuntimeError("transport died"), _http_502], ids=["runtime", "http502"]
)


@TRANSPORT_FAULTS
def test_list_default_recovers_tools_when_resources_take_the_session_down(capsys, make_exc):
    """A request task failing outside the hook cancels it and closes the session;
    plain `list` lists the tools again in a fresh session and notes the fault."""
    exc = make_exc()
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])

    rc, _ = _run_list(_ns("acme"), preview=fake.killing(exc), fake=fake)

    assert rc == 0
    assert fake.opened == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"name": "whoami", "description": "Who am I"}]
    assert captured.err.startswith(f"[list] note: could not list resources: {type(exc).__name__}: ")
    assert "Traceback" not in captured.err


@TRANSPORT_FAULTS
def test_list_resources_session_failure_exits_1_without_traceback(capsys, make_exc):
    exc = make_exc()
    fake = _FakeSessions()

    rc, _ = _run_list(_ns("acme", resources=True), lister=fake.killing(exc), fake=fake)

    assert rc == 1
    assert fake.opened == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"[list] error: could not list resources: {type(exc).__name__}: ")
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("exc_type", [anyio.ClosedResourceError, anyio.BrokenResourceError])
def test_list_default_recovers_from_a_dead_stream_inside_the_hook(capsys, exc_type):
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])

    rc, _ = _run_list(_ns("acme"), preview=AsyncMock(side_effect=exc_type()), fake=fake)

    assert rc == 0
    assert fake.opened == 2
    assert capsys.readouterr().err.startswith(f"[list] note: could not list resources: {exc_type.__name__}")


@pytest.mark.parametrize("resources", [False, True], ids=["default", "resources"])
def test_list_hook_bug_propagates_unrecovered(resources):
    """A bug in the listing code is not a transport failure: it must traceback."""
    fake = _FakeSessions(
        tools=[_mock_tool("whoami", "Who am I")],
        capabilities=mcp_types.ServerCapabilities(resources=mcp_types.ResourcesCapability()),
    )
    fake.session.list_resources = AsyncMock(
        return_value=mcp_types.ListResourcesResult(resources=[mcp_types.Resource(uri="demo://a", name="a")])
    )

    with (
        patch("mcpgen._bridge._dump_listing", side_effect=KeyError("uri")),
        pytest.raises(ExceptionGroup) as caught,
    ):
        _run_list(_ns("acme", resources=resources), fake=fake)

    assert [type(e) for e in caught.value.exceptions] == [KeyError]
    assert fake.opened == 1


def test_list_close_failure_after_the_hook_finished_propagates():
    """Resources were listed; a failing close fails `list` as it would a tools-only listing."""
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])
    fake.close_with = ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("close failed")])
    preview = AsyncMock(return_value=(RESOURCES, False, TEMPLATES, False))

    with pytest.raises(ExceptionGroup, match="unhandled errors"):
        _run_list(_ns("acme"), preview=preview, fake=fake)

    assert fake.opened == 1


@pytest.mark.parametrize("argv", [["list", "acme"], ["list", "acme", "--resources"]], ids=["default", "resources"])
def test_list_auth_failure_mid_session_reaches_main_handler(capsys, argv):
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])
    lister = fake.killing(_bridge.ReauthenticationRequired("credential is dead. Run: mcpgen login acme"))

    rc, _ = _run_list(argv, lister=lister, preview=lister, fake=fake)

    assert rc == 1
    assert fake.opened == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[mcpgen] error: credential is dead. Run: mcpgen login acme\n"


def test_list_default_hint_on_an_empty_truncated_first_page(capsys):
    preview = AsyncMock(return_value=([], True, [], False))

    rc, _ = _run_list(_ns("acme"), preview=preview, tools=[_mock_tool("whoami", "Who am I")])

    assert rc == 0
    assert capsys.readouterr().err == (
        "[list] 0+ resource(s) and 0 resource template(s) available; re-run with `--resources` to list them\n"
    )


def test_list_default_times_out_on_a_silent_resources_list(capsys):
    async def never_answers(s, **kwargs):
        await asyncio.sleep(30)

    with patch("mcpgen.cli.PREVIEW_TIMEOUT", 0.05):
        rc, fake = _run_list(_ns("acme"), preview=never_answers, tools=[_mock_tool("whoami", "Who am I")])

    assert rc == 0
    assert fake.opened == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [{"name": "whoami", "description": "Who am I"}]
    assert captured.err == "[list] note: could not list resources: TimeoutError: no answer within 0.05s\n"


def test_list_inventory_tools_failure_before_hook_propagates():
    """Only a failure after the hook started is a resource failure worth recovering from."""
    fake = _FakeSessions()
    fake.session.list_tools = AsyncMock(side_effect=RuntimeError("tools/list died"))
    preview = AsyncMock()

    with (
        patch("mcpgen.cli._bridge.session", fake),
        patch("mcpgen.cli._bridge.preview_resource_inventory", preview),
        pytest.raises(ExceptionGroup),
    ):
        asyncio.run(_list_inventory("acme", scope="all", strict=False))

    assert fake.opened == 1
    preview.assert_not_awaited()


def test_list_inventory_interrupt_is_not_recovered():
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])

    with (
        patch("mcpgen.cli._bridge.session", fake),
        patch("mcpgen.cli._bridge.preview_resource_inventory", AsyncMock(side_effect=KeyboardInterrupt)),
        pytest.raises(BaseExceptionGroup) as caught,
    ):
        asyncio.run(_list_inventory("acme", scope="all", strict=False))

    assert [type(e) for e in caught.value.exceptions] == [KeyboardInterrupt]
    assert fake.opened == 1


def test_list_inventory_grouped_interrupt_from_outside_is_not_recovered():
    """Real anyio delivers Ctrl-C as BaseExceptionGroup([KeyboardInterrupt]) after
    cancelling the body; that is an interrupt, not a transport failure."""
    fake = _FakeSessions(tools=[_mock_tool("whoami", "Who am I")])

    async def interrupted(s, **kwargs):
        fake.close_with = BaseExceptionGroup("unhandled errors in a TaskGroup", [KeyboardInterrupt()])
        fake._tg.cancel_scope.cancel()
        await anyio.sleep_forever()

    with (
        patch("mcpgen.cli._bridge.session", fake),
        patch("mcpgen.cli._bridge.preview_resource_inventory", interrupted),
        pytest.raises(BaseExceptionGroup) as caught,
    ):
        asyncio.run(_list_inventory("acme", scope="all", strict=False))

    assert [type(e) for e in caught.value.exceptions] == [KeyboardInterrupt]
    assert fake.opened == 1


def test_list_resources_flag_is_wired(capsys):
    lister = AsyncMock(return_value=(LISTED_RESOURCES[1:], []))

    rc, _ = _run_list(["list", "acme", "--resources"], lister=lister)

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {
        "resources": [{"uri": "dimension://all", "name": "dimensions"}],
        "resourceTemplates": [],
    }
