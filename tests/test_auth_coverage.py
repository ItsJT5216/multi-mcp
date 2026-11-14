"""
Extended auth coverage tests.

Covers:
  6b. Auth Coverage:
  - MCP endpoints use MULTI_MCP_API_KEY
  - Mutating routes require distinct MULTI_MCP_MANAGEMENT_API_KEY auth
  - Auth uses hmac.compare_digest (not == comparison)
  - SSE transport accepts Bearer header OR ?token= query param (deprecated)
  - Invalid / missing tokens get 401
"""

import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from src.multimcp.mcp_client import MCPClientManager
from src.multimcp.mcp_proxy import MCPProxyServer
from src.multimcp.multi_mcp import MultiMCP


# ─── helpers ────────────────────────────────────────────────────────────────

API_KEY = "test-key-abc123"
MANAGEMENT_API_KEY = "management-key-def456"


def _build_app_with_auth() -> MultiMCP:
    """Create a MultiMCP instance with auth enabled and a minimal proxy."""
    app = MultiMCP(
        transport="sse",
        host="127.0.0.1",
        port=18090,
        api_key=API_KEY,
        management_api_key=MANAGEMENT_API_KEY,
    )
    # Wire a proxy so endpoint handlers don't fail with AttributeError
    app.proxy = MCPProxyServer(MCPClientManager())
    return app


def _build_app_no_auth() -> MultiMCP:
    """Create a MultiMCP instance with no API key (auth disabled)."""
    app = MultiMCP(
        transport="sse",
        host="127.0.0.1",
        port=18091,
    )
    app.proxy = MCPProxyServer(MCPClientManager())
    return app


def _make_request(path: str, query: str = "", headers: list = None) -> Request:
    """Build a minimal Starlette Request for unit-testing _check_auth."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode() if isinstance(query, str) else query,
        "headers": headers or [],
    }
    return Request(scope)


# ─── TestHmacAuth: unit tests for _check_auth ────────────────────────────────


class TestHmacAuth:
    """Unit tests that verify _check_auth uses hmac.compare_digest internally."""

    def test_check_auth_uses_hmac_compare_digest(self):
        """hmac.compare_digest is called during token validation (not string ==)."""
        app = _build_app_with_auth()
        request = _make_request(
            "/health",
            headers=[(b"authorization", f"Bearer {API_KEY}".encode())],
        )
        with patch("hmac.compare_digest", wraps=hmac.compare_digest) as mock_digest:
            result = app._check_auth(request)
            # Auth should succeed with the correct token
            assert result is None
            # compare_digest must have been called at least once
            assert mock_digest.called

    def test_valid_bearer_token_accepted(self):
        """_check_auth returns None (pass-through) for a correct Bearer token."""
        app = _build_app_with_auth()
        request = _make_request(
            "/health",
            headers=[(b"authorization", f"Bearer {API_KEY}".encode())],
        )
        result = app._check_auth(request)
        assert result is None

    def test_invalid_bearer_token_rejected(self):
        """_check_auth returns a 401 JSONResponse for a wrong Bearer token."""
        app = _build_app_with_auth()
        request = _make_request(
            "/health",
            headers=[(b"authorization", b"Bearer wrong-token")],
        )
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    def test_missing_auth_header_rejected(self):
        """Non-SSE endpoint with no Authorization header → 401."""
        app = _build_app_with_auth()
        request = _make_request("/health")
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    def test_malformed_bearer_header_rejected(self):
        """'Authorization: token' (missing 'Bearer ' prefix) → 401."""
        app = _build_app_with_auth()
        # "token" keyword without "Bearer " prefix
        request = _make_request(
            "/health",
            headers=[(b"authorization", f"token {API_KEY}".encode())],
        )
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    def test_missing_bearer_prefix_in_header(self):
        """Raw API key in Authorization header (no 'Bearer ') → 401."""
        app = _build_app_with_auth()
        request = _make_request(
            "/health",
            headers=[(b"authorization", API_KEY.encode())],
        )
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    def test_auth_disabled_returns_none_always(self):
        """When api_key is not set, _check_auth always returns None."""
        app = _build_app_no_auth()
        # No header, no query param — should still pass
        request = _make_request("/health")
        assert app._check_auth(request) is None


# ─── TestEndpointAuth: integration tests via TestClient ──────────────────────


class TestEndpointAuth:
    """Test that every HTTP endpoint in create_starlette_app enforces auth."""

    @pytest.fixture
    def auth_client(self):
        """TestClient for an app with auth enabled."""
        app = _build_app_with_auth()
        return TestClient(app.create_starlette_app(), raise_server_exceptions=False)

    # ── Individual endpoint 401 checks ────────────────────────────────────

    def test_mcp_servers_requires_auth(self, auth_client):
        """GET /mcp_servers without auth → 401."""
        response = auth_client.get("/mcp_servers")
        assert response.status_code == 401

    def test_mcp_tools_requires_auth(self, auth_client):
        """GET /mcp_tools without auth → 401."""
        response = auth_client.get("/mcp_tools")
        assert response.status_code == 401

    def test_health_requires_auth(self, auth_client):
        """GET /health without auth → 401."""
        response = auth_client.get("/health")
        assert response.status_code == 401

    def test_mcp_control_requires_auth(self, auth_client):
        """POST /mcp_control without auth → 401."""
        response = auth_client.post(
            "/mcp_control", json={"action": "enable", "server": "test"}
        )
        assert response.status_code == 401

    def test_sse_requires_auth(self):
        """GET /sse without any auth → _check_auth returns 401.

        The SSE endpoint blocks indefinitely on a real connection, so we test
        the auth logic directly via _check_auth.
        """
        app = _build_app_with_auth()
        request = _make_request("/sse")
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    def test_messages_requires_auth(self, auth_client):
        """POST /messages/test without auth → 401."""
        response = auth_client.post("/messages/test", content=b"{}")
        assert response.status_code == 401

    # ── SSE: token query param (deprecated) ───────────────────────────────

    def test_sse_accepts_token_query_param(self):
        """GET /sse?token=<valid-key> passes auth check (returns None from _check_auth).

        The SSE endpoint blocks indefinitely in a real connection, so we test the
        auth logic directly via _check_auth rather than through TestClient.
        """
        app = _build_app_with_auth()
        request = _make_request(
            "/sse",
            query=f"token={API_KEY}",
        )
        # Auth must pass — no 401 response object returned
        result = app._check_auth(request)
        assert result is None

    def test_sse_rejects_invalid_token_query_param(self):
        """GET /sse?token=wrong → _check_auth returns 401."""
        app = _build_app_with_auth()
        request = _make_request("/sse", query="token=wrong-token")
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    def test_sse_rejects_missing_token_query_param(self):
        """GET /sse with no token at all → _check_auth returns 401."""
        app = _build_app_with_auth()
        request = _make_request("/sse")
        result = app._check_auth(request)
        assert result is not None
        assert result.status_code == 401

    # ── Valid Bearer accepted on all non-SSE endpoints ────────────────────

    def test_all_endpoints_accept_valid_bearer(self):
        """All non-SSE endpoints return non-401 when provided a valid Bearer token."""
        app = _build_app_with_auth()
        client = TestClient(app.create_starlette_app(), raise_server_exceptions=False)
        valid_headers = {"Authorization": f"Bearer {API_KEY}"}

        endpoints = [
            ("GET", "/mcp_servers"),
            ("GET", "/mcp_tools"),
            ("GET", "/health"),
        ]
        for method, path in endpoints:
            if method == "GET":
                response = client.get(path, headers=valid_headers)
            else:
                response = client.post(path, headers=valid_headers, json={})
            assert response.status_code != 401, (
                f"{method} {path} returned 401 despite valid Bearer token"
            )

    def test_mcp_control_accepts_valid_bearer(self):
        """POST /mcp_control with the management Bearer returns non-401."""
        app = _build_app_with_auth()
        client = TestClient(app.create_starlette_app(), raise_server_exceptions=False)
        response = client.post(
            "/mcp_control",
            headers={"Authorization": f"Bearer {MANAGEMENT_API_KEY}"},
            json={"action": "enable", "server": "nonexistent"},
        )
        # Auth passed — the handler may return 404 since 'nonexistent' isn't registered
        assert response.status_code != 401

    def test_mcp_servers_post_accepts_valid_bearer(self):
        """POST /mcp_servers with the management Bearer returns non-401."""
        app = _build_app_with_auth()
        client = TestClient(app.create_starlette_app(), raise_server_exceptions=False)
        response = client.post(
            "/mcp_servers",
            headers={"Authorization": f"Bearer {MANAGEMENT_API_KEY}"},
            json={"mcpServers": {}},
        )
        # Auth passed — the handler may return 4xx for invalid payload, but not 401
        assert response.status_code != 401

    def test_client_bearer_cannot_manage_servers(self):
        """The client Bearer cannot cross into the operator-only process boundary."""
        app = _build_app_with_auth()
        client = TestClient(app.create_starlette_app(), raise_server_exceptions=False)
        response = client.post(
            "/mcp_servers",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"mcpServers": {"attacker": {"command": "sh"}}},
        )
        assert response.status_code == 401

    def test_client_bearer_cannot_control_servers(self):
        """The client Bearer cannot enable even preconfigured backend processes."""
        app = _build_app_with_auth()
        client = TestClient(app.create_starlette_app(), raise_server_exceptions=False)
        response = client.post(
            "/mcp_control",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"action": "enable", "server": "test"},
        )
        assert response.status_code == 401


# ─── TestAuthDisabled: no auth when key not set ──────────────────────────────


class TestAuthDisabled:
    """Without a client key, read-only endpoints stay open and management stays closed."""

    @pytest.fixture
    def no_auth_client(self):
        """TestClient for an app with auth disabled."""
        app = _build_app_no_auth()
        return TestClient(app.create_starlette_app(), raise_server_exceptions=False)

    def test_no_auth_when_key_not_set(self, no_auth_client):
        """All non-SSE endpoints return non-401 without any auth header."""
        endpoints = [
            ("GET", "/mcp_servers"),
            ("GET", "/mcp_tools"),
            ("GET", "/health"),
        ]
        for method, path in endpoints:
            response = no_auth_client.get(path)
            assert response.status_code != 401, (
                f"Expected no auth on {method} {path} but got 401"
            )

    def test_health_open_without_auth(self, no_auth_client):
        """GET /health returns 200 when auth is disabled."""
        response = no_auth_client.get("/health")
        assert response.status_code == 200

    def test_mcp_servers_open_without_auth(self, no_auth_client):
        """GET /mcp_servers returns non-401 when auth is disabled."""
        response = no_auth_client.get("/mcp_servers")
        assert response.status_code != 401

    def test_mcp_tools_open_without_auth(self, no_auth_client):
        """GET /mcp_tools returns non-401 when auth is disabled."""
        response = no_auth_client.get("/mcp_tools")
        assert response.status_code != 401

    def test_management_closed_without_operator_key(self, no_auth_client):
        """POST /mcp_servers returns 403 instead of accepting unauthenticated commands."""
        response = no_auth_client.post(
            "/mcp_servers",
            json={"mcpServers": {"attacker": {"command": "sh"}}},
        )
        assert response.status_code == 403

    def test_auth_enabled_property_false_when_no_key(self):
        """auth_enabled is False when no api_key is set."""
        app = _build_app_no_auth()
        assert app.auth_enabled is False

    def test_auth_enabled_property_true_when_key_set(self):
        """auth_enabled is True when api_key is configured."""
        app = _build_app_with_auth()
        assert app.auth_enabled is True

    def test_sse_open_without_auth(self):
        """GET /sse without auth → _check_auth returns None when auth is disabled."""
        app = _build_app_no_auth()
        request = _make_request("/sse")
        result = app._check_auth(request)
        # Auth disabled — _check_auth always returns None (no 401)
        assert result is None
