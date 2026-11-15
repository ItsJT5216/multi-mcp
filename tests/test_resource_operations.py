"""Tests for resource operations: subscribe, unsubscribe, read, list.

Client-facing resource keys are valid proxy URIs bound to a backend identity.
The proxy resolves those keys and passes each backend its original raw URI.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from mcp import types
from mcp.shared.exceptions import McpError

from src.multimcp.mcp_client import MCPClientManager
from src.multimcp.mcp_proxy import (
    RESOURCE_PROVENANCE_META_KEY,
    MCPProxyServer,
    ResourceMapping,
)


RAW_URI = "resource://weather/data"


def _make_proxy_with_resource():
    """Create a proxy with a resource stored under its public proxy URI."""
    manager = MCPClientManager()
    proxy = MCPProxyServer(manager)
    mock_client = AsyncMock()
    mock_resource = types.Resource(uri=RAW_URI, name="Weather Data")
    public_uri = proxy._make_resource_uri("weather", RAW_URI)
    proxy.resource_to_server[public_uri] = ResourceMapping(
        server_name="weather", client=mock_client, resource=mock_resource
    )
    return proxy, mock_client


def _public_uri(proxy: MCPProxyServer) -> str:
    return proxy._make_resource_uri("weather", RAW_URI)


class TestSubscribeResource:
    """Subscribe resolves a public URI and passes the raw URI to its backend."""

    @pytest.mark.asyncio
    async def test_subscribe_forwards_raw_uri(self):
        proxy, mock_client = _make_proxy_with_resource()
        mock_client.subscribe_resource = AsyncMock()

        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = _public_uri(proxy)

        result = await proxy._subscribe_resource(req)

        mock_client.subscribe_resource.assert_called_once()
        call_arg = mock_client.subscribe_resource.call_args[0][0]
        assert str(call_arg) == RAW_URI
        assert result.root.meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "weather",
            "uri": RAW_URI,
        }

    @pytest.mark.asyncio
    async def test_subscribe_unknown_resource_returns_error(self):
        proxy, _ = _make_proxy_with_resource()
        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = proxy._make_resource_uri(
            "weather", "resource://nonexistent/foo"
        )

        with pytest.raises(McpError):
            await proxy._subscribe_resource(req)

    @pytest.mark.asyncio
    async def test_subscribe_disconnected_client_returns_error(self):
        proxy, _ = _make_proxy_with_resource()
        proxy.resource_to_server[_public_uri(proxy)].client = None

        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = _public_uri(proxy)

        with pytest.raises(McpError):
            await proxy._subscribe_resource(req)


class TestUnsubscribeResource:
    """Unsubscribe resolves a public URI and passes the raw URI to its backend."""

    @pytest.mark.asyncio
    async def test_unsubscribe_forwards_raw_uri(self):
        proxy, mock_client = _make_proxy_with_resource()
        mock_client.unsubscribe_resource = AsyncMock()

        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = _public_uri(proxy)

        result = await proxy._unsubscribe_resource(req)

        mock_client.unsubscribe_resource.assert_called_once()
        call_arg = mock_client.unsubscribe_resource.call_args[0][0]
        assert str(call_arg) == RAW_URI
        assert result.root.meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "weather",
            "uri": RAW_URI,
        }

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_resource_returns_error(self):
        proxy, _ = _make_proxy_with_resource()
        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = proxy._make_resource_uri(
            "weather", "resource://nonexistent/foo"
        )

        with pytest.raises(McpError):
            await proxy._unsubscribe_resource(req)


class TestReadResource:
    """Read resolves a public URI and preserves provenance in its result."""

    @pytest.mark.asyncio
    async def test_read_forwards_raw_uri(self):
        proxy, mock_client = _make_proxy_with_resource()
        mock_client.read_resource = AsyncMock(
            return_value=types.ReadResourceResult(
                contents=[
                    types.TextResourceContents(
                        uri=RAW_URI, text="sunny", mimeType="text/plain"
                    )
                ]
            )
        )

        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = _public_uri(proxy)

        result = await proxy._read_resource(req)

        mock_client.read_resource.assert_called_once()
        call_arg = mock_client.read_resource.call_args[0][0]
        assert str(call_arg) == RAW_URI
        assert str(result.root.contents[0].uri) == _public_uri(proxy)
        assert result.root.contents[0].meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "weather",
            "uri": RAW_URI,
        }

    @pytest.mark.asyncio
    async def test_read_disconnected_client_returns_error(self):
        proxy, _ = _make_proxy_with_resource()
        proxy.resource_to_server[_public_uri(proxy)].client = None

        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = _public_uri(proxy)

        with pytest.raises(McpError):
            await proxy._read_resource(req)

    @pytest.mark.asyncio
    async def test_read_unknown_resource_returns_error(self):
        proxy, _ = _make_proxy_with_resource()
        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = proxy._make_resource_uri(
            "weather", "resource://nonexistent/foo"
        )

        with pytest.raises(McpError):
            await proxy._read_resource(req)

    @pytest.mark.asyncio
    async def test_read_client_exception_returns_error(self):
        proxy, mock_client = _make_proxy_with_resource()
        mock_client.read_resource = AsyncMock(side_effect=Exception("connection lost"))

        req = MagicMock()
        req.params = MagicMock()
        req.params.uri = _public_uri(proxy)

        with pytest.raises(McpError):
            await proxy._read_resource(req)


class TestListResources:
    """List resources returns namespaced names, URIs, and provenance."""

    @pytest.mark.asyncio
    async def test_list_returns_namespaced_name(self):
        proxy, _ = _make_proxy_with_resource()
        result = await proxy._list_resources(MagicMock())
        resources = result.root.resources
        assert len(resources) == 1
        # Name should be namespaced for disambiguation
        assert resources[0].name == "weather__Weather Data"

    @pytest.mark.asyncio
    async def test_list_exposes_public_uri_and_provenance(self):
        proxy, _ = _make_proxy_with_resource()
        result = await proxy._list_resources(MagicMock())
        resources = result.root.resources
        assert str(resources[0].uri) == _public_uri(proxy)
        assert resources[0].meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "weather",
            "uri": RAW_URI,
        }

    @pytest.mark.asyncio
    async def test_resource_mapping_type_consistency(self):
        proxy, _ = _make_proxy_with_resource()
        for key, value in proxy.resource_to_server.items():
            assert isinstance(value, ResourceMapping), (
                f"resource_to_server[{key}] is {type(value).__name__}, expected ResourceMapping"
            )

    @pytest.mark.asyncio
    async def test_empty_resource_list(self):
        manager = MCPClientManager()
        proxy = MCPProxyServer(manager)
        result = await proxy._list_resources(MagicMock())
        assert result.root.resources == []

    @pytest.mark.asyncio
    async def test_multiple_servers_resources_listed(self):
        proxy, _ = _make_proxy_with_resource()
        news_uri = "resource://news/headlines"
        mock_resource2 = types.Resource(uri=news_uri, name="Headlines")
        proxy.resource_to_server[
            proxy._make_resource_uri("news", news_uri)
        ] = ResourceMapping(
            server_name="news", client=AsyncMock(), resource=mock_resource2
        )
        result = await proxy._list_resources(MagicMock())
        assert len(result.root.resources) == 2
