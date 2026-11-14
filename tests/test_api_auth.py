"""
Tests for API key authentication on HTTP endpoints and SSE connections.

Following TDD methodology:
- RED: Write failing tests first
- GREEN: Implement minimal code to pass
- REFACTOR: Clean up while keeping tests green
"""

import pytest
import pytest_asyncio
from starlette.testclient import TestClient
from src.multimcp.multi_mcp import MultiMCP
from src.multimcp.mcp_proxy import MCPProxyServer
from src.multimcp.mcp_client import MCPClientManager


CLIENT_API_KEY = "test-secret-key-12345"
MANAGEMENT_API_KEY = "test-management-key-67890"


@pytest_asyncio.fixture
async def auth_app():
    """Create MultiMCP app with auth enabled."""
    # Create app with API key auth enabled
    app = MultiMCP(
        transport="sse",
        host="127.0.0.1",
        port=8085,
        api_key=CLIENT_API_KEY,
        management_api_key=MANAGEMENT_API_KEY,
    )

    # Mock client manager and proxy
    app.proxy = await MCPProxyServer.create(MCPClientManager())

    # Build Starlette app (normally done in start_sse_server)
    # We'll need to extract this into a method for testing
    return app


@pytest_asyncio.fixture
async def no_auth_app():
    """Create MultiMCP app without auth (default behavior)."""
    app = MultiMCP(transport="sse", host="127.0.0.1", port=8085)

    app.proxy = await MCPProxyServer.create(MCPClientManager())
    return app


class TestHTTPEndpointAuth:
    """Test authentication on HTTP API endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint_requires_auth_when_enabled(self, auth_app):
        """Health endpoint should require Bearer token when auth enabled."""
        # This will fail initially - we haven't implemented auth yet
        client = TestClient(auth_app.create_starlette_app())

        # Request without auth header
        response = client.get("/health")
        assert response.status_code == 401
        assert "error" in response.json()
        assert "unauthorized" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_health_endpoint_accepts_valid_token(self, auth_app):
        """Health endpoint should accept valid Bearer token."""
        client = TestClient(auth_app.create_starlette_app())

        # Request with valid auth header
        response = client.get(
            "/health", headers={"Authorization": f"Bearer {CLIENT_API_KEY}"}
        )
        assert response.status_code == 200
        assert "status" in response.json()

    @pytest.mark.asyncio
    async def test_health_endpoint_rejects_invalid_token(self, auth_app):
        """Health endpoint should reject invalid Bearer token."""
        client = TestClient(auth_app.create_starlette_app())

        # Request with wrong token
        response = client.get(
            "/health", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_health_endpoint_no_auth_when_disabled(self, no_auth_app):
        """Health endpoint should not require auth when disabled."""
        client = TestClient(no_auth_app.create_starlette_app())

        # Request without auth header should work
        response = client.get("/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_mcp_servers_endpoint_requires_auth(self, auth_app):
        """GET /mcp_servers should require auth when enabled."""
        client = TestClient(auth_app.create_starlette_app())

        response = client.get("/mcp_servers")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_mcp_tools_endpoint_requires_auth(self, auth_app):
        """GET /mcp_tools should require auth when enabled."""
        client = TestClient(auth_app.create_starlette_app())

        response = client.get("/mcp_tools")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_post_mcp_servers_requires_auth(self, auth_app):
        """POST /mcp_servers should require auth when enabled."""
        client = TestClient(auth_app.create_starlette_app())

        response = client.post("/mcp_servers", json={"mcpServers": {}})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_mcp_servers_requires_auth(self, auth_app):
        """DELETE /mcp_servers/{name} should require auth when enabled."""
        client = TestClient(auth_app.create_starlette_app())

        response = client.delete("/mcp_servers/test")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_client_token_cannot_mutate_servers(self, auth_app):
        """The ordinary MCP client credential must not authorize server creation."""
        client = TestClient(auth_app.create_starlette_app())

        response = client.post(
            "/mcp_servers",
            headers={"Authorization": f"Bearer {CLIENT_API_KEY}"},
            json={"mcpServers": {}},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_management_token_reaches_handler(self, auth_app):
        """The distinct operator credential preserves authenticated management."""
        client = TestClient(auth_app.create_starlette_app())

        response = client.post(
            "/mcp_servers",
            headers={"Authorization": f"Bearer {MANAGEMENT_API_KEY}"},
            json={"mcpServers": {}},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_management_disabled_without_key(self, no_auth_app):
        """Mutating routes fail closed when no management credential is configured."""
        client = TestClient(no_auth_app.create_starlette_app())

        response = client.post(
            "/mcp_servers",
            json={
                "mcpServers": {
                    "attacker": {"command": "sh", "args": ["-c", "id"]}
                }
            },
        )
        assert response.status_code == 403


class TestSSEAuthQueryParam:
    """Test authentication on SSE endpoint using query parameter."""

    @pytest.mark.asyncio
    async def test_sse_endpoint_requires_token_query_param(self, auth_app):
        """SSE endpoint should require ?token=xxx query param when auth enabled."""
        # Test auth check directly without full connection
        from starlette.requests import Request
        from starlette.datastructures import Headers, URL

        # Mock request without token
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/sse",
            "query_string": b"",
            "headers": [],
        }
        request = Request(scope)

        error_response = auth_app._check_auth(request)
        assert error_response is not None
        assert error_response.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_endpoint_accepts_valid_token_query_param(self, auth_app):
        """SSE endpoint should accept valid token in query param."""
        from starlette.requests import Request

        # Mock request with valid token
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/sse",
            "query_string": b"token=test-secret-key-12345",
            "headers": [],
        }
        request = Request(scope)

        error_response = auth_app._check_auth(request)
        assert error_response is None  # Auth passed

    @pytest.mark.asyncio
    async def test_sse_endpoint_rejects_invalid_token_query_param(self, auth_app):
        """SSE endpoint should reject invalid token in query param."""
        from starlette.requests import Request

        # Mock request with wrong token
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/sse",
            "query_string": b"token=wrong-token",
            "headers": [],
        }
        request = Request(scope)

        error_response = auth_app._check_auth(request)
        assert error_response is not None
        assert error_response.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_endpoint_no_auth_when_disabled(self, no_auth_app):
        """SSE endpoint should not require token when auth disabled."""
        from starlette.requests import Request

        # Mock request without token
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/sse",
            "query_string": b"",
            "headers": [],
        }
        request = Request(scope)

        error_response = no_auth_app._check_auth(request)
        assert error_response is None  # Auth disabled, should pass


class TestAuthConfiguration:
    """Test authentication configuration options."""

    def test_api_key_from_settings(self):
        """API key should be configurable via MCPSettings."""
        app = MultiMCP(api_key="my-secret-key")
        assert app.settings.api_key == "my-secret-key"

    def test_api_key_from_env_var(self, monkeypatch):
        """API key should be loadable from MULTI_MCP_API_KEY env var."""
        monkeypatch.setenv("MULTI_MCP_API_KEY", "env-secret-key")
        app = MultiMCP()
        assert app.settings.api_key == "env-secret-key"

    def test_api_key_default_is_none(self):
        """API key should default to None (no auth)."""
        app = MultiMCP()
        assert app.settings.api_key is None

    def test_management_api_key_from_env_var(self, monkeypatch):
        """Operator auth should load from MULTI_MCP_MANAGEMENT_API_KEY."""
        monkeypatch.setenv("MULTI_MCP_MANAGEMENT_API_KEY", "operator-secret")
        app = MultiMCP()
        assert app.settings.management_api_key == "operator-secret"

    def test_management_key_must_be_distinct(self):
        """Client credentials cannot be reused to gain management privileges."""
        with pytest.raises(ValueError, match="must differ"):
            MultiMCP(
                transport="sse",
                api_key="shared-secret",
                management_api_key="shared-secret",
            )

    def test_non_loopback_sse_requires_client_key(self):
        """Externally bound SSE servers must reject missing client authentication."""
        with pytest.raises(ValueError, match="require MULTI_MCP_API_KEY"):
            MultiMCP(transport="sse", host="0.0.0.0")

    def test_loopback_sse_remains_available_without_client_key(self):
        """Local SSE development remains available without network authentication."""
        app = MultiMCP(transport="sse", host="127.0.0.1")
        assert app.auth_enabled is False

    def test_explicit_network_escape_hatch(self):
        """Explicitly isolated deployments may opt into unauthenticated network binds."""
        app = MultiMCP(
            transport="sse",
            host="0.0.0.0",
            allow_unauthenticated_network=True,
        )
        assert app.auth_enabled is False

    def test_auth_enabled_property(self):
        """Should have property to check if auth is enabled."""
        app_with_auth = MultiMCP(api_key="secret")
        app_no_auth = MultiMCP()

        assert app_with_auth.auth_enabled is True
        assert app_no_auth.auth_enabled is False


class TestAuthMiddleware:
    """Test authentication middleware behavior."""

    @pytest.mark.asyncio
    async def test_middleware_checks_bearer_token(self, auth_app):
        """Middleware should extract and validate Bearer token."""
        client = TestClient(auth_app.create_starlette_app())

        # Valid format but wrong token
        response = client.get("/health", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_middleware_handles_malformed_auth_header(self, auth_app):
        """Middleware should handle malformed Authorization headers."""
        client = TestClient(auth_app.create_starlette_app())

        # Missing 'Bearer' prefix
        response = client.get(
            "/health", headers={"Authorization": CLIENT_API_KEY}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_middleware_skips_when_auth_disabled(self, no_auth_app):
        """Middleware should allow all requests when auth disabled."""
        client = TestClient(no_auth_app.create_starlette_app())

        # No auth header, but should work
        response = client.get("/health")
        assert response.status_code == 200
