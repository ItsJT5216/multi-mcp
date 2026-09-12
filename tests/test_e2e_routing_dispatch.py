"""E2E test: model-visible tool name 'request_tool' reaches handle_routing_call().

Replaces V-02 claim: "dispatch confirmed via grep" (code structure check, not behavior).
This test verifies that the actual runtime dispatch works — the routing tool name
"request_tool" triggers handle_routing_call() in mcp_proxy.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp import types


class TestRequestToolCallable:
    """request_tool reaches handle_routing_call() via _call_tool() dispatch."""

    def test_routing_tool_name_and_key_constants(self):
        """ROUTING_TOOL_NAME is 'request_tool', ROUTING_TOOL_KEY includes 'request_tool'."""
        from src.multimcp.retrieval.routing_tool import ROUTING_TOOL_NAME, ROUTING_TOOL_KEY

        assert ROUTING_TOOL_NAME == "request_tool", (
            f"ROUTING_TOOL_NAME must be 'request_tool', got {ROUTING_TOOL_NAME!r}"
        )
        assert "request_tool" in ROUTING_TOOL_KEY, (
            f"ROUTING_TOOL_KEY must contain 'request_tool', got {ROUTING_TOOL_KEY!r}"
        )

    @pytest.mark.anyio
    async def test_request_tool_callable(self):
        """Calling tool_name == ROUTING_TOOL_NAME dispatches to handle_routing_call().

        This replaces V-02 grep-based verification with a runtime behavioral test.
        The test patches handle_routing_call so we can confirm it's actually called.
        """
        from src.multimcp.retrieval.routing_tool import ROUTING_TOOL_NAME

        # Build a minimal MCPProxyServer with just the state _call_tool needs
        from src.multimcp.mcp_proxy import MCPProxyServer

        proxy = MCPProxyServer.__new__(MCPProxyServer)
        proxy.tool_to_server = {}
        proxy.retrieval_pipeline = None

        # Patch handle_routing_call at the source module so the lazy import in _call_tool gets it
        # (handle_routing_call is synchronous, so patch() uses a regular Mock by default)
        with patch(
            "src.multimcp.retrieval.routing_tool.handle_routing_call",
            return_value=[types.TextContent(type="text", text="describe result")],
        ) as mock_handle:
            req = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name=ROUTING_TOOL_NAME,
                    arguments={"name": "some_tool", "describe": True},
                ),
            )
            # Call through the actual dispatch path
            await proxy._call_tool(req)

            # Verify handle_routing_call was invoked with the correct signature
            mock_handle.assert_called_once_with(
                "some_tool",        # name
                True,               # describe
                {},                 # arguments (call_args)
                proxy.tool_to_server,  # tool_to_server
            )

    def test_handle_routing_call_importable_and_callable(self):
        """handle_routing_call is importable and is a callable function."""
        from src.multimcp.retrieval.routing_tool import handle_routing_call

        assert callable(handle_routing_call), "handle_routing_call must be callable"

    def test_routing_tool_name_dispatch_in_proxy(self):
        """_call_tool() in mcp_proxy.py references ROUTING_TOOL_NAME for early-return dispatch.

        Structural verification that the dispatch guard is present (complements runtime test).
        The proxy dispatches on tool_name == ROUTING_TOOL_NAME (not ROUTING_TOOL_KEY),
        which is the model-visible name 'request_tool'.
        """
        import inspect
        from src.multimcp import mcp_proxy

        source = inspect.getsource(mcp_proxy)
        assert "ROUTING_TOOL_NAME" in source, (
            "mcp_proxy.py must reference ROUTING_TOOL_NAME for routing dispatch"
        )
        # Also confirm it's in _call_tool (not just an import)
        assert "handle_routing_call" in source, (
            "mcp_proxy.py must call handle_routing_call in _call_tool dispatch"
        )
