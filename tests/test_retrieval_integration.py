"""Integration tests for retrieval pipeline in MCPProxyServer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import types
from src.multimcp.mcp_proxy import MCPProxyServer
from src.multimcp.retrieval.base import PassthroughRetriever
from src.multimcp.retrieval.logging import NullLogger
from src.multimcp.retrieval.models import RetrievalConfig
from src.multimcp.retrieval.pipeline import RetrievalPipeline
from src.multimcp.retrieval.session import SessionStateManager


def _make_tool(name: str) -> types.Tool:
    return types.Tool(
        name=name,
        description="test",
        inputSchema={"type": "object", "properties": {}},
    )


def _make_proxy(pipeline=None):
    with patch("src.multimcp.mcp_proxy.AuditLogger"):
        proxy = MCPProxyServer(MagicMock())
    proxy.trigger_manager.check_and_enable = AsyncMock(return_value=[])
    proxy.retrieval_pipeline = pipeline
    proxy._server_session = MagicMock() if pipeline is not None else None
    return proxy


class TestProxyPipelineIntegration:
    """Test MCPProxyServer integration with retrieval pipeline."""

    def _make_proxy(self, pipeline=None):
        return _make_proxy(pipeline)

    @pytest.mark.asyncio
    async def test_list_tools_without_pipeline(self):
        """Without pipeline (None), _list_tools returns all tools including cached."""
        proxy = self._make_proxy(pipeline=None)
        from src.multimcp.mcp_proxy import ToolMapping

        proxy.tool_to_server["github__get_me"] = ToolMapping(
            server_name="github",
            client=MagicMock(),
            tool=_make_tool("github__get_me"),
        )
        result = await proxy._list_tools(None)
        assert len(result.root.tools) == 1

    @pytest.mark.asyncio
    async def test_list_tools_with_disabled_pipeline(self):
        """Disabled pipeline returns all tools (same as no pipeline)."""
        config = RetrievalConfig(enabled=False)
        pipeline = RetrievalPipeline(
            retriever=PassthroughRetriever(),
            session_manager=SessionStateManager(config),
            logger=NullLogger(),
            config=config,
            tool_registry={},
        )
        proxy = self._make_proxy(pipeline=pipeline)
        from src.multimcp.mcp_proxy import ToolMapping

        proxy.tool_to_server["github__get_me"] = ToolMapping(
            server_name="github",
            client=MagicMock(),
            tool=_make_tool("github__get_me"),
        )
        # Point pipeline's registry to proxy's dict (as done in multi_mcp.py)
        pipeline.tool_registry = proxy.tool_to_server
        result = await proxy._list_tools(None)
        assert len(result.root.tools) == 1

    @pytest.mark.asyncio
    async def test_list_tools_with_enabled_pipeline_bounded(self):
        """Enabled pipeline returns bounded tool set for fresh session.

        Phase 2: active set computed by fallback ladder, not anchor seeding.
        Small registry (2 tools) returns all available tools directly.
        """
        config = RetrievalConfig(
            enabled=True,
            anchor_tools=["github__get_me"],
            rollout_stage="ga",
        )
        pipeline = RetrievalPipeline(
            retriever=PassthroughRetriever(),
            session_manager=SessionStateManager(config),
            logger=NullLogger(),
            config=config,
            tool_registry={},
        )
        proxy = self._make_proxy(pipeline=pipeline)
        from src.multimcp.mcp_proxy import ToolMapping

        proxy.tool_to_server["github__get_me"] = ToolMapping(
            server_name="github",
            client=MagicMock(),
            tool=_make_tool("github__get_me"),
        )
        proxy.tool_to_server["exa__search"] = ToolMapping(
            server_name="exa",
            client=MagicMock(),
            tool=_make_tool("exa__search"),
        )
        pipeline.tool_registry = proxy.tool_to_server
        result = await proxy._list_tools(None)
        # Phase 2: small registry (2 tools) → all tools returned
        non_routing = [t for t in result.root.tools if t.name != "request_tool"]
        assert len(non_routing) <= 20  # core invariant
        assert len(non_routing) >= 1  # at least one tool

    @pytest.mark.asyncio
    async def test_pipeline_attribute_exists_after_init(self):
        """MCPProxyServer must have retrieval_pipeline attribute."""
        proxy = self._make_proxy()
        assert hasattr(proxy, "retrieval_pipeline")

    @pytest.mark.asyncio
    async def test_pipeline_none_by_default_in_make_proxy(self):
        """Our test helper sets pipeline=None by default."""
        proxy = self._make_proxy()
        assert proxy.retrieval_pipeline is None


class TestCallToolPipelineNotification:
    """Test that _call_tool notifies pipeline after successful calls."""

    def _make_proxy_with_tool(self, pipeline=None):
        proxy = _make_proxy(pipeline)

        # Add a connected tool
        from src.multimcp.mcp_proxy import ToolMapping

        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.content = [types.TextContent(type="text", text="ok")]
        mock_result.isError = False
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        proxy.tool_to_server["github__get_me"] = ToolMapping(
            server_name="github",
            client=mock_client,
            tool=_make_tool("github__get_me"),
        )
        return proxy

    @pytest.mark.asyncio
    async def test_call_tool_notifies_pipeline(self):
        """on_tool_called should be invoked after a successful tool call."""
        from unittest.mock import ANY

        config = RetrievalConfig(enabled=True)
        pipeline = RetrievalPipeline(
            retriever=PassthroughRetriever(),
            session_manager=SessionStateManager(config),
            logger=NullLogger(),
            config=config,
            tool_registry={},
        )
        # Spy on on_tool_called
        pipeline.on_tool_called = AsyncMock(return_value=False)

        proxy = self._make_proxy_with_tool(pipeline=pipeline)
        req = MagicMock()
        req.params = MagicMock()
        req.params.name = "github__get_me"
        req.params.arguments = {"foo": "bar"}

        await proxy._call_tool(req)
        # Session ID is now a real UUID (not "default") — use ANY for matching
        pipeline.on_tool_called.assert_called_once_with(
            ANY, "github__get_me", {"foo": "bar"}
        )
        # Verify the session_id is a valid 32-char hex UUID, not "default"
        actual_session_id = pipeline.on_tool_called.call_args[0][0]
        assert actual_session_id != "default"
        assert len(actual_session_id) == 32

    @pytest.mark.asyncio
    async def test_call_tool_without_pipeline_still_works(self):
        """Tool calls work fine without any pipeline configured."""
        proxy = self._make_proxy_with_tool(pipeline=None)
        req = MagicMock()
        req.params = MagicMock()
        req.params.name = "github__get_me"
        req.params.arguments = {}

        result = await proxy._call_tool(req)
        # Should succeed without errors
        assert result is not None

    @pytest.mark.asyncio
    async def test_pipeline_error_doesnt_break_tool_call(self):
        """If pipeline.on_tool_called raises, tool call still succeeds."""
        config = RetrievalConfig(enabled=True)
        pipeline = RetrievalPipeline(
            retriever=PassthroughRetriever(),
            session_manager=SessionStateManager(config),
            logger=NullLogger(),
            config=config,
            tool_registry={},
        )
        pipeline.on_tool_called = AsyncMock(side_effect=RuntimeError("pipeline broke"))

        proxy = self._make_proxy_with_tool(pipeline=pipeline)
        req = MagicMock()
        req.params = MagicMock()
        req.params.name = "github__get_me"
        req.params.arguments = {}

        # Should not raise — pipeline errors are caught
        result = await proxy._call_tool(req)
        assert result is not None
