"""
Tests for input sanitization: command validation, URL validation (SSRF),
environment variable filtering, and API error response behavior.
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock
from starlette.testclient import TestClient

from src.multimcp.mcp_client import (
    _validate_command,
    _validate_url,
    _filter_env,
    PROTECTED_ENV_VARS,
)
from src.multimcp.multi_mcp import MultiMCP
from src.multimcp.mcp_proxy import MCPProxyServer
from src.multimcp.mcp_client import MCPClientManager


MANAGEMENT_API_KEY = "input-test-management-key"
MANAGEMENT_HEADERS = {"Authorization": f"Bearer {MANAGEMENT_API_KEY}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_no_debug():
    """MultiMCP app with debug=False (default)."""
    app = MultiMCP(
        transport="sse",
        host="127.0.0.1",
        port=18091,
        management_api_key=MANAGEMENT_API_KEY,
    )
    app.proxy = await MCPProxyServer.create(MCPClientManager())
    return app


@pytest_asyncio.fixture
async def app_debug():
    """MultiMCP app with debug=True."""
    app = MultiMCP(
        transport="sse",
        host="127.0.0.1",
        port=18092,
        debug=True,
        management_api_key=MANAGEMENT_API_KEY,
    )
    app.proxy = await MCPProxyServer.create(MCPClientManager())
    return app


# ---------------------------------------------------------------------------
# 1. TestCommandValidation
# ---------------------------------------------------------------------------

class TestCommandValidation:
    """Tests for _validate_command() allowlist enforcement."""

    def test_allowed_commands_pass(self):
        """Default-allowed commands should not raise."""
        for cmd in ("node", "python", "uvx", "docker"):
            _validate_command(cmd)  # must not raise

    def test_disallowed_command_raises(self):
        """Commands not in the allowlist must raise ValueError."""
        for cmd in ("curl", "wget", "rm", "cat"):
            with pytest.raises(ValueError):
                _validate_command(cmd)

    def test_full_path_with_allowed_basename_passes(self):
        """Full paths to allowed basenames should pass — enables nvm, pyenv, etc."""
        _validate_command("/usr/bin/node")  # basename 'node' is in allowlist

    def test_full_path_with_allowed_basename_bash(self):
        """Full path to bash should pass — bash is now in DEFAULT_ALLOWED_COMMANDS."""
        _validate_command("/usr/bin/bash")  # bash is allowed, basename matches

    def test_env_override_allows_custom_command(self, monkeypatch):
        """MULTI_MCP_ALLOWED_COMMANDS env var overrides the default allowlist."""
        monkeypatch.setenv("MULTI_MCP_ALLOWED_COMMANDS", "bash,sh")
        # bash and sh are now allowed
        _validate_command("bash")  # must not raise
        _validate_command("sh")    # must not raise
        # node is no longer in the custom allowlist
        with pytest.raises(ValueError):
            _validate_command("node")


# ---------------------------------------------------------------------------
# 2. TestUrlValidation
# ---------------------------------------------------------------------------

class TestUrlValidation:
    """Tests for _validate_url() scheme, hostname, and DNS validation."""

    @pytest.mark.asyncio
    async def test_non_http_scheme_blocked(self):
        """Non-http/https schemes must be rejected."""
        with pytest.raises(ValueError):
            await _validate_url("ftp://example.com/file")

    @pytest.mark.asyncio
    async def test_file_scheme_blocked(self):
        """file:// scheme must be rejected."""
        with pytest.raises(ValueError):
            await _validate_url("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_public_url_accepted(self):
        """A URL resolving to a public IP must pass validation."""
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(
                return_value=[(None, None, None, None, ("8.8.8.8", 0))]
            )
            # Should not raise
            await _validate_url("http://example.com/api")
            mock_loop.return_value.getaddrinfo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_private_ip_127_accepted(self):
        """127.0.0.1 must be accepted — private-IP blocking removed."""
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(
                return_value=[(None, None, None, None, ("127.0.0.1", 0))]
            )
            # Should not raise
            await _validate_url("http://127.0.0.1:9080/sse")
            mock_loop.return_value.getaddrinfo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_localhost_accepted(self):
        """localhost must be accepted — private-IP blocking removed."""
        with patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = AsyncMock(
                return_value=[(None, None, None, None, ("127.0.0.1", 0))]
            )
            # Should not raise
            await _validate_url("http://localhost:8080/mcp")
            mock_loop.return_value.getaddrinfo.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. TestEnvFiltering
# ---------------------------------------------------------------------------

class TestEnvFiltering:
    """Tests for _filter_env() removal of protected environment variables."""

    def test_path_removed_from_env(self):
        """PATH must be stripped from server-provided env."""
        result = _filter_env({"PATH": "/usr/bin", "MY_VAR": "value"})
        assert "PATH" not in result
        assert result == {"MY_VAR": "value"}

    def test_ld_preload_removed(self):
        """LD_PRELOAD must be stripped."""
        result = _filter_env({"LD_PRELOAD": "/evil.so", "MY_VAR": "value"})
        assert "LD_PRELOAD" not in result
        assert result == {"MY_VAR": "value"}

    def test_pythonpath_removed(self):
        """PYTHONPATH must be stripped."""
        result = _filter_env({"PYTHONPATH": "/evil", "MY_VAR": "value"})
        assert "PYTHONPATH" not in result
        assert result == {"MY_VAR": "value"}

    def test_all_protected_vars_removed(self):
        """All PROTECTED_ENV_VARS must be stripped when present."""
        env = {var: "val" for var in PROTECTED_ENV_VARS}
        env["SAFE_VAR"] = "keep"
        result = _filter_env(env)
        for var in PROTECTED_ENV_VARS:
            assert var not in result
        assert result == {"SAFE_VAR": "keep"}

    def test_non_protected_vars_preserved(self):
        """Non-protected env vars must be returned unchanged."""
        result = _filter_env({"API_KEY": "secret", "TOKEN": "abc"})
        assert result == {"API_KEY": "secret", "TOKEN": "abc"}

    def test_empty_env_returns_empty(self):
        """An empty dict must pass through unchanged."""
        assert _filter_env({}) == {}


# ---------------------------------------------------------------------------
# 4. TestApiErrorResponses
# ---------------------------------------------------------------------------

class TestApiErrorResponses:
    """Tests for HTTP API error handling and debug mode."""

    @pytest.mark.asyncio
    async def test_malformed_json_returns_400(self, app_no_debug):
        """POST /mcp_servers with invalid JSON must return 400."""
        client = TestClient(app_no_debug.create_starlette_app())
        response = client.post(
            "/mcp_servers",
            content=b"not-valid-json{{{",
            headers={**MANAGEMENT_HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 400
        body = response.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_missing_mcp_servers_field_returns_422(self, app_no_debug):
        """POST /mcp_servers with JSON that lacks 'mcpServers' must return 422."""
        client = TestClient(app_no_debug.create_starlette_app())
        response = client.post(
            "/mcp_servers",
            json={"other": "data"},
            headers=MANAGEMENT_HEADERS,
        )
        assert response.status_code == 422
        body = response.json()
        assert "error" in body

    @pytest.mark.asyncio
    async def test_error_detail_hidden_when_debug_false(self, app_no_debug):
        """When debug=False, error detail must be None (no stack trace leakage)."""
        # Force a 500 by making proxy.unregister_client raise
        client_mgr = app_no_debug.proxy.client_manager
        mock_session = AsyncMock()
        client_mgr.clients["test-srv"] = mock_session

        # Make unregister_client raise so we hit the 500 path in handle_mcp_servers DELETE
        original_unregister = app_no_debug.proxy.unregister_client
        async def _boom(name):
            raise RuntimeError("internal crash detail")
        app_no_debug.proxy.unregister_client = _boom

        tc = TestClient(app_no_debug.create_starlette_app())
        response = tc.delete(
            "/mcp_servers/test-srv", headers=MANAGEMENT_HEADERS
        )
        assert response.status_code == 500
        body = response.json()
        assert body.get("detail") is None

        # Restore
        app_no_debug.proxy.unregister_client = original_unregister

    @pytest.mark.asyncio
    async def test_error_detail_shown_when_debug_true(self, app_debug):
        """When debug=True, error detail must be included in 500 response."""
        client_mgr = app_debug.proxy.client_manager
        mock_session = AsyncMock()
        client_mgr.clients["test-srv"] = mock_session

        async def _boom(name):
            raise RuntimeError("internal crash detail")
        app_debug.proxy.unregister_client = _boom

        tc = TestClient(app_debug.create_starlette_app())
        response = tc.delete(
            "/mcp_servers/test-srv", headers=MANAGEMENT_HEADERS
        )
        assert response.status_code == 500
        body = response.json()
        assert body.get("detail") is not None
        assert "internal crash detail" in body["detail"]

        # Restore
        app_debug.proxy.unregister_client = AsyncMock()
