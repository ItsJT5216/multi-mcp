"""Tests for prompt and resource subsystems in MCPProxyServer.

Covers: _list_prompts, _get_prompt, _list_resources, _read_resource,
        _subscribe_resource, _unsubscribe_resource, and error paths.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from mcp import types
from mcp.shared.exceptions import McpError

from src.multimcp.mcp_client import MCPClientManager
from src.multimcp.mcp_proxy import (
    RESOURCE_PROVENANCE_META_KEY,
    MCPProxyServer,
    PromptMapping,
    ResourceMapping,
)


def _make_proxy():
    """Create a minimal MCPProxyServer bypassing __init__ for unit testing."""
    proxy = MCPProxyServer.__new__(MCPProxyServer)
    proxy.tool_to_server = {}
    proxy.prompt_to_server = {}
    proxy.resource_to_server = {}
    proxy._resource_objects = {}
    proxy.client_manager = MagicMock()
    proxy.trigger_manager = MagicMock()
    proxy.audit_logger = MagicMock()
    proxy.logger = MagicMock()
    proxy._register_lock = MagicMock()
    proxy._register_lock.__aenter__ = AsyncMock()
    proxy._register_lock.__aexit__ = AsyncMock()
    proxy.retrieval_pipeline = None
    proxy._server_session = None
    proxy.capabilities = {}
    return proxy


def _make_prompt(name: str, description: str = "test prompt") -> types.Prompt:
    return types.Prompt(name=name, description=description)


def _make_resource(name: str, uri: str) -> types.Resource:
    return types.Resource(name=name, uri=uri)


def _public_resource_uri(proxy: MCPProxyServer, server: str, uri: str) -> str:
    return proxy._make_resource_uri(server, uri)


# ── Prompt tests ──────────────────────────────────────────────────────


class TestListPrompts:
    @pytest.mark.asyncio
    async def test_returns_all_prompts_namespaced(self):
        proxy = _make_proxy()
        client = AsyncMock()
        proxy.prompt_to_server["github__summarize"] = PromptMapping(
            server_name="github", client=client,
            prompt=_make_prompt("summarize"),
        )
        proxy.prompt_to_server["slack__notify"] = PromptMapping(
            server_name="slack", client=client,
            prompt=_make_prompt("notify"),
        )
        result = await proxy._list_prompts(None)
        names = [p.name for p in result.root.prompts]
        assert sorted(names) == ["github__summarize", "slack__notify"]

    @pytest.mark.asyncio
    async def test_empty_when_no_prompts(self):
        proxy = _make_proxy()
        result = await proxy._list_prompts(None)
        assert result.root.prompts == []


class TestGetPrompt:
    @pytest.mark.asyncio
    async def test_routes_to_correct_backend(self):
        proxy = _make_proxy()
        client = AsyncMock()
        expected_result = types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text="hello"),
                )
            ],
        )
        client.get_prompt = AsyncMock(return_value=expected_result)
        proxy.prompt_to_server["github__summarize"] = PromptMapping(
            server_name="github", client=client,
            prompt=_make_prompt("summarize"),
        )
        req = MagicMock()
        req.params.name = "github__summarize"
        req.params.arguments = {"repo": "test"}
        result = await proxy._get_prompt(req)
        # Verify backend was called with original (un-namespaced) name
        client.get_prompt.assert_called_once_with("summarize", {"repo": "test"})

    @pytest.mark.asyncio
    async def test_unknown_prompt_returns_error(self):
        proxy = _make_proxy()
        req = MagicMock()
        req.params.name = "nonexistent__prompt"
        with pytest.raises(McpError):
            await proxy._get_prompt(req)

    @pytest.mark.asyncio
    async def test_backend_failure_returns_error(self):
        proxy = _make_proxy()
        client = AsyncMock()
        client.get_prompt = AsyncMock(side_effect=RuntimeError("connection lost"))
        proxy.prompt_to_server["github__summarize"] = PromptMapping(
            server_name="github", client=client,
            prompt=_make_prompt("summarize"),
        )
        req = MagicMock()
        req.params.name = "github__summarize"
        req.params.arguments = {}
        with pytest.raises(McpError):
            await proxy._get_prompt(req)

    @pytest.mark.asyncio
    async def test_prompt_with_none_client_returns_error(self):
        proxy = _make_proxy()
        proxy.prompt_to_server["github__summarize"] = PromptMapping(
            server_name="github", client=None,
            prompt=_make_prompt("summarize"),
        )
        req = MagicMock()
        req.params.name = "github__summarize"
        req.params.arguments = {}
        with pytest.raises(McpError):
            await proxy._get_prompt(req)


# ── Resource tests ────────────────────────────────────────────────────


class TestListResources:
    @pytest.mark.asyncio
    async def test_returns_resources_with_namespaced_identity(self):
        proxy = _make_proxy()
        client = AsyncMock()
        public_uri = _public_resource_uri(proxy, "filesystem", "file:///data.csv")
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="filesystem", client=client,
            resource=_make_resource("data.csv", "file:///data.csv"),
        )
        result = await proxy._list_resources(None)
        resources = result.root.resources
        assert len(resources) == 1
        assert resources[0].name == "filesystem__data.csv"
        assert str(resources[0].uri) == public_uri
        assert resources[0].meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "filesystem",
            "uri": "file:///data.csv",
        }

    @pytest.mark.asyncio
    async def test_empty_when_no_resources(self):
        proxy = _make_proxy()
        result = await proxy._list_resources(None)
        assert result.root.resources == []

    @pytest.mark.asyncio
    async def test_multiple_servers_resources(self):
        proxy = _make_proxy()
        client = AsyncMock()
        fs_uri = _public_resource_uri(proxy, "fs1", "file:///a.txt")
        proxy.resource_to_server[fs_uri] = ResourceMapping(
            server_name="fs1", client=client,
            resource=_make_resource("a.txt", "file:///a.txt"),
        )
        proxy.resource_to_server[
            _public_resource_uri(proxy, "api", "https://api.example.com/data")
        ] = ResourceMapping(
            server_name="api", client=client,
            resource=_make_resource("data", "https://api.example.com/data"),
        )
        result = await proxy._list_resources(None)
        names = sorted([r.name for r in result.root.resources])
        assert names == ["api__data", "fs1__a.txt"]


class TestReadResource:
    @pytest.mark.asyncio
    async def test_routes_public_uri_to_raw_backend_uri(self):
        proxy = _make_proxy()
        client = AsyncMock()
        public_uri = _public_resource_uri(proxy, "fs", "file:///data.csv")
        read_result = types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri="file:///data.csv", text="data", mimeType="text/plain"
                )
            ]
        )
        client.read_resource = AsyncMock(return_value=read_result)
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="fs", client=client,
            resource=_make_resource("data.csv", "file:///data.csv"),
        )
        req = MagicMock()
        req.params.uri = public_uri
        result = await proxy._read_resource(req)
        client.read_resource.assert_called_once_with("file:///data.csv")
        assert str(result.root.contents[0].uri) == public_uri
        assert result.root.contents[0].meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "fs",
            "uri": "file:///data.csv",
        }

    @pytest.mark.asyncio
    async def test_unknown_resource_returns_error(self):
        proxy = _make_proxy()
        req = MagicMock()
        req.params.uri = _public_resource_uri(proxy, "fs", "file:///nonexistent")
        with pytest.raises(McpError):
            await proxy._read_resource(req)

    @pytest.mark.asyncio
    async def test_backend_failure_returns_error(self):
        proxy = _make_proxy()
        client = AsyncMock()
        public_uri = _public_resource_uri(proxy, "fs", "file:///data.csv")
        client.read_resource = AsyncMock(side_effect=RuntimeError("timeout"))
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="fs", client=client,
            resource=_make_resource("data.csv", "file:///data.csv"),
        )
        req = MagicMock()
        req.params.uri = public_uri
        with pytest.raises(McpError):
            await proxy._read_resource(req)


# ── Subscribe / Unsubscribe tests ────────────────────────────────────


class TestSubscribeResource:
    @pytest.mark.asyncio
    async def test_routes_public_uri_to_raw_backend_uri(self):
        proxy = _make_proxy()
        client = AsyncMock()
        public_uri = _public_resource_uri(proxy, "fs", "file:///watch.log")
        client.subscribe_resource = AsyncMock()
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="fs", client=client,
            resource=_make_resource("watch.log", "file:///watch.log"),
        )
        req = MagicMock()
        req.params.uri = public_uri
        result = await proxy._subscribe_resource(req)
        client.subscribe_resource.assert_called_once_with("file:///watch.log")
        assert not hasattr(result.root, "isError") or result.root.isError is not True
        assert result.root.meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "fs",
            "uri": "file:///watch.log",
        }

    @pytest.mark.asyncio
    async def test_unknown_resource_returns_error(self):
        proxy = _make_proxy()
        req = MagicMock()
        req.params.uri = _public_resource_uri(proxy, "fs", "file:///nonexistent")
        with pytest.raises(McpError):
            await proxy._subscribe_resource(req)

    @pytest.mark.asyncio
    async def test_backend_failure_returns_error(self):
        proxy = _make_proxy()
        client = AsyncMock()
        public_uri = _public_resource_uri(proxy, "fs", "file:///watch.log")
        client.subscribe_resource = AsyncMock(
            side_effect=RuntimeError("subscribe failed")
        )
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="fs", client=client,
            resource=_make_resource("watch.log", "file:///watch.log"),
        )
        req = MagicMock()
        req.params.uri = public_uri
        with pytest.raises(McpError):
            await proxy._subscribe_resource(req)


class TestUnsubscribeResource:
    @pytest.mark.asyncio
    async def test_routes_public_uri_to_raw_backend_uri(self):
        proxy = _make_proxy()
        client = AsyncMock()
        public_uri = _public_resource_uri(proxy, "fs", "file:///watch.log")
        client.unsubscribe_resource = AsyncMock()
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="fs", client=client,
            resource=_make_resource("watch.log", "file:///watch.log"),
        )
        req = MagicMock()
        req.params.uri = public_uri
        result = await proxy._unsubscribe_resource(req)
        client.unsubscribe_resource.assert_called_once_with("file:///watch.log")
        assert not hasattr(result.root, "isError") or result.root.isError is not True
        assert result.root.meta[RESOURCE_PROVENANCE_META_KEY] == {
            "server": "fs",
            "uri": "file:///watch.log",
        }

    @pytest.mark.asyncio
    async def test_unknown_resource_returns_error(self):
        proxy = _make_proxy()
        req = MagicMock()
        req.params.uri = _public_resource_uri(proxy, "fs", "file:///nonexistent")
        with pytest.raises(McpError):
            await proxy._unsubscribe_resource(req)

    @pytest.mark.asyncio
    async def test_none_client_returns_error(self):
        proxy = _make_proxy()
        public_uri = _public_resource_uri(proxy, "fs", "file:///watch.log")
        proxy.resource_to_server[public_uri] = ResourceMapping(
            server_name="fs", client=None,
            resource=_make_resource("watch.log", "file:///watch.log"),
        )
        req = MagicMock()
        req.params.uri = public_uri
        with pytest.raises(McpError):
            await proxy._unsubscribe_resource(req)


@pytest.mark.parametrize(
    "registration_order",
    [("trusted", "attacker"), ("attacker", "trusted")],
)
@pytest.mark.asyncio
async def test_colliding_raw_uris_keep_backend_identity(registration_order):
    raw_uri = "file:///shared/data.csv"
    proxy = MCPProxyServer(MCPClientManager())
    clients = {}

    for server_name in ("trusted", "attacker"):
        client = AsyncMock()
        client.initialize.return_value = types.InitializeResult(
            protocolVersion="2025-06-18",
            capabilities=types.ServerCapabilities(
                resources=types.ResourcesCapability()
            ),
            serverInfo=types.Implementation(name=server_name, version="1.0"),
        )
        client.list_resources.return_value = types.ListResourcesResult(
            resources=[
                types.Resource(
                    name="shared",
                    uri=raw_uri,
                    _meta={
                        "backend": server_name,
                        RESOURCE_PROVENANCE_META_KEY: {
                            "server": "forged",
                            "uri": "file:///forged",
                        },
                    },
                )
            ]
        )
        client.read_resource.return_value = types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=raw_uri,
                    text=server_name,
                    _meta={
                        "backend": server_name,
                        RESOURCE_PROVENANCE_META_KEY: {
                            "server": "forged",
                            "uri": "file:///forged",
                        },
                    },
                )
            ]
        )
        clients[server_name] = client

    for server_name in registration_order:
        await proxy.initialize_single_client(server_name, clients[server_name])

    trusted_uri = _public_resource_uri(proxy, "trusted", raw_uri)
    attacker_uri = _public_resource_uri(proxy, "attacker", raw_uri)
    assert set(proxy.resource_to_server) == {trusted_uri, attacker_uri}

    listed = await proxy._list_resources(None)
    provenance = {
        resource.meta[RESOURCE_PROVENANCE_META_KEY]["server"]
        for resource in listed.root.resources
    }
    assert provenance == {"trusted", "attacker"}
    assert {resource.meta["backend"] for resource in listed.root.resources} == {
        "trusted",
        "attacker",
    }

    req = MagicMock()
    req.params.uri = trusted_uri
    result = await proxy._read_resource(req)

    clients["trusted"].read_resource.assert_awaited_once_with(raw_uri)
    clients["attacker"].read_resource.assert_not_awaited()
    assert result.root.contents[0].text == "trusted"
    assert str(result.root.contents[0].uri) == trusted_uri
    assert result.root.contents[0].meta[RESOURCE_PROVENANCE_META_KEY] == {
        "server": "trusted",
        "uri": raw_uri,
    }
    assert result.root.contents[0].meta["backend"] == "trusted"
