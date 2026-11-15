"""
Tests for Core Stabilization (Task 1):
- Unregister filter bug for prompts/resources
- Prompt/resource namespacing
- Asyncio lock around register/unregister
- Namespacing separator changed to __
- Name validation (no __ allowed in names)
- Resource key safety
"""

import pytest
import pytest_asyncio
from mcp.types import Tool, Prompt, Resource, TextContent
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session
from src.multimcp.mcp_proxy import MCPProxyServer
from src.multimcp.mcp_client import MCPClientManager


@pytest.fixture
def test_prompt():
    """Mock prompt for testing."""
    return Prompt(
        name="test_prompt",
        description="A test prompt",
        arguments=[],
    )


@pytest.fixture
def test_resource():
    """Mock resource for testing."""
    return Resource(
        uri="file:///test/resource",
        name="test_resource",
        description="A test resource",
        mimeType="text/plain",
    )


@pytest_asyncio.fixture
async def server_with_prompts(test_prompt):
    """Server that provides prompts."""
    server = Server("PromptServer")

    @server.list_prompts()
    async def _():
        return [test_prompt]

    @server.get_prompt()
    async def _(name, arguments):
        if name == test_prompt.name:
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": "Test prompt response"},
                    }
                ]
            }
        return None

    return server


@pytest_asyncio.fixture
async def server_with_resources(test_resource):
    """Server that provides resources."""
    server = Server("ResourceServer")

    @server.list_resources()
    async def _():
        return [test_resource]

    @server.read_resource()
    async def _(uri):
        if uri == test_resource.uri:
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": "Test resource content",
                    }
                ]
            }
        return None

    return server


@pytest_asyncio.fixture
async def server_with_tool():
    """Server that provides a tool."""
    server = Server("ToolServer")
    tool = Tool(
        name="test_tool",
        description="A test tool",
        inputSchema={"type": "object", "properties": {}},
    )

    @server.list_tools()
    async def _():
        return [tool]

    @server.call_tool()
    async def _(tool_name, arguments):
        return []

    return server


@pytest.mark.asyncio
async def test_unregister_removes_prompts_correctly(server_with_prompts, test_prompt):
    """
    Test that unregister_client correctly removes prompts.
    This tests the bug fix for the filter condition.
    """
    async with create_connected_server_and_client_session(
        server_with_prompts
    ) as client:
        client_manager = MCPClientManager()
        client_manager.clients = {"PromptServer": client}

        proxy = await MCPProxyServer.create(client_manager)

        # Verify prompt is registered
        assert len(proxy.prompt_to_server) == 1, "Should have one prompt registered"

        # Unregister the server
        await proxy.unregister_client("PromptServer")

        # Verify prompt is removed
        assert len(proxy.prompt_to_server) == 0, (
            "Prompt should be removed after unregister"
        )


@pytest.mark.asyncio
async def test_unregister_removes_resources_correctly(
    server_with_resources, test_resource
):
    """
    Test that unregister_client correctly removes resources.
    This tests the bug fix for the filter condition.
    """
    async with create_connected_server_and_client_session(
        server_with_resources
    ) as client:
        client_manager = MCPClientManager()
        client_manager.clients = {"ResourceServer": client}

        proxy = await MCPProxyServer.create(client_manager)

        # Verify resource is registered
        assert len(proxy.resource_to_server) == 1, "Should have one resource registered"

        # Unregister the server
        await proxy.unregister_client("ResourceServer")

        # Verify resource is removed
        assert len(proxy.resource_to_server) == 0, (
            "Resource should be removed after unregister"
        )


@pytest.mark.asyncio
async def test_prompts_are_namespaced(server_with_prompts, test_prompt):
    """
    Test that prompts are namespaced with server__prompt format.
    """
    async with create_connected_server_and_client_session(
        server_with_prompts
    ) as client:
        client_manager = MCPClientManager()
        client_manager.clients = {"PromptServer": client}

        proxy = await MCPProxyServer.create(client_manager)

        # Check internal mapping uses namespaced key
        assert len(proxy.prompt_to_server) == 1
        keys = list(proxy.prompt_to_server.keys())
        assert keys[0] == "PromptServer__test_prompt", (
            f"Expected 'PromptServer__test_prompt', got '{keys[0]}'"
        )


@pytest.mark.asyncio
async def test_resources_are_namespaced(server_with_resources, test_resource):
    """
    Test that resources are stored by backend-bound public URI.
    """
    async with create_connected_server_and_client_session(
        server_with_resources
    ) as client:
        client_manager = MCPClientManager()
        client_manager.clients = {"ResourceServer": client}

        proxy = await MCPProxyServer.create(client_manager)

        # Check internal mapping uses the proxy's valid namespaced URI as key
        assert len(proxy.resource_to_server) == 1
        keys = list(proxy.resource_to_server.keys())
        expected_uri = proxy._make_resource_uri(
            "ResourceServer", str(test_resource.uri)
        )
        assert keys[0] == expected_uri, (
            f"Expected '{expected_uri}', got '{keys[0]}'"
        )


@pytest.mark.asyncio
async def test_tools_use_double_underscore_separator(server_with_tool):
    """
    Test that tools use __ separator.
    """
    async with create_connected_server_and_client_session(server_with_tool) as client:
        client_manager = MCPClientManager()
        client_manager.clients = {"ToolServer": client}

        proxy = await MCPProxyServer.create(client_manager)

        # Check internal mapping uses __ separator
        assert len(proxy.tool_to_server) == 1
        keys = list(proxy.tool_to_server.keys())
        tool_name = keys[0]
        assert tool_name == "ToolServer__test_tool", (
            f"Expected 'ToolServer__test_tool' with __ separator, got '{tool_name}'"
        )
        assert "__" in tool_name, "Tool name should use __ separator"


@pytest.mark.asyncio
async def test_name_validation_rejects_double_underscore():
    """
    Test that server/tool names containing __ are rejected during registration.
    """
    server = Server("Invalid__Server")
    tool = Tool(
        name="invalid__tool",
        description="Tool with __ in name",
        inputSchema={"type": "object", "properties": {}},
    )

    @server.list_tools()
    async def _():
        return [tool]

    async with create_connected_server_and_client_session(server) as client:
        client_manager = MCPClientManager()
        proxy = MCPProxyServer(client_manager)

        # Should raise ValueError when trying to initialize with __ in name
        with pytest.raises(ValueError, match="cannot contain.*__"):
            await proxy.initialize_single_client("Invalid__Server", client)


@pytest.mark.asyncio
async def test_concurrent_register_unregister_is_safe(server_with_tool):
    """
    Test that concurrent register/unregister operations are thread-safe.
    """
    import asyncio

    async with create_connected_server_and_client_session(server_with_tool) as client:
        client_manager = MCPClientManager()
        proxy = MCPProxyServer(client_manager)

        # Register and unregister concurrently
        async def register_task():
            for i in range(10):
                await proxy.register_client(f"Server{i}", client)
                await asyncio.sleep(0.001)

        async def unregister_task():
            await asyncio.sleep(0.005)  # Let some register first
            for i in range(5):
                await proxy.unregister_client(f"Server{i}")
                await asyncio.sleep(0.001)

        # Run concurrently
        await asyncio.gather(register_task(), unregister_task())

        # Should have 5 registered (Server5-9) and 5 unregistered (Server0-4)
        assert len(client_manager.clients) == 5, (
            "Concurrent operations should be safe with proper locking"
        )


@pytest.mark.asyncio
async def test_resource_uri_with_separator_is_escaped():
    """
    Test that resource URIs containing :: are handled safely.
    """
    server = Server("URIServer")
    resource_with_separator = Resource(
        uri="file:///path::with::separator",
        name="safe_name",
        description="Resource with :: in URI",
        mimeType="text/plain",
    )

    @server.list_resources()
    async def _():
        return [resource_with_separator]

    @server.read_resource()
    async def _(uri):
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "content"}]}

    async with create_connected_server_and_client_session(server) as client:
        client_manager = MCPClientManager()
        client_manager.clients = {"URIServer": client}

        proxy = await MCPProxyServer.create(client_manager)

        # Separator-like content is encoded inside the public resource URI
        assert len(proxy.resource_to_server) == 1
        keys = list(proxy.resource_to_server.keys())
        expected_uri = proxy._make_resource_uri(
            "URIServer", "file:///path::with::separator"
        )
        assert keys[0] == expected_uri, (
            f"Key should be namespaced URI. Got: {keys[0]}"
        )
