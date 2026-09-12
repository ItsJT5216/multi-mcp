"""Proof tests for two real-use improvements:

1. Parallel startup connections — initialize_remote_clients,
   _connect_always_on_servers, and _connect_json_config_servers connect/init
   servers concurrently, so startup latency is bounded by the slowest server
   (O(max)) instead of the sum of all servers (O(sum)).
   Timing proofs: N servers x DELAY each must finish well under N*DELAY.
   Regression guards: per-server failure isolation semantics are preserved.

2. Per-server tool-call timeout — ServerConfig.tool_call_timeout_seconds.
   Default None preserves previous behavior exactly (no kwarg passed at all).
   When set, a hung backend returns an isError timeout response instead of
   stalling the request forever, and the failure counts toward the proxy's
   circuit breaker.
"""

import asyncio
import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from mcp import types
from mcp.server import Server
from mcp.shared.memory import create_connected_server_and_client_session

from src.multimcp.mcp_client import MCPClientManager
from src.multimcp.mcp_proxy import MCPProxyServer, ToolMapping
from src.multimcp.multi_mcp import MultiMCP
from src.multimcp.yaml_config import (
    MultiMCPConfig,
    ServerConfig,
    load_config,
    save_config,
)

DELAY = 0.25  # per-server simulated connect/init latency
N_SERVERS = 3
SEQUENTIAL_FLOOR = DELAY * N_SERVERS          # 0.75s if connections were serial
PARALLEL_CEILING = DELAY * N_SERVERS - DELAY  # 0.5s — must beat serial by >= one DELAY


class SlowInitClient:
    """Fake ClientSession whose MCP handshake takes DELAY seconds."""

    def __init__(self, delay: float = DELAY, fail: bool = False):
        self.delay = delay
        self.fail = fail

    async def initialize(self):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("handshake failed")
        return MagicMock(
            capabilities=MagicMock(tools=None, prompts=None, resources=None)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Improvement 1a: MCPProxyServer.initialize_remote_clients is concurrent
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_remote_clients_runs_concurrently():
    """N slow clients must initialize in ~max(delay), not sum(delays)."""
    manager = MCPClientManager()
    manager.clients = {f"srv{i}": SlowInitClient() for i in range(N_SERVERS)}

    proxy = MCPProxyServer(manager)
    start = time.monotonic()
    await proxy.initialize_remote_clients()
    elapsed = time.monotonic() - start

    assert elapsed < PARALLEL_CEILING, (
        f"initialize_remote_clients took {elapsed:.2f}s for {N_SERVERS} servers "
        f"x {DELAY}s each — sequential floor is {SEQUENTIAL_FLOOR}s, "
        f"parallel must finish under {PARALLEL_CEILING}s"
    )
    # All clients initialized and capabilities recorded
    assert set(proxy.capabilities.keys()) == {f"srv{i}" for i in range(N_SERVERS)}
    assert len(manager.clients) == N_SERVERS


@pytest.mark.asyncio
async def test_initialize_remote_clients_failure_isolated():
    """Regression guard: a failing client is removed; healthy clients survive."""
    manager = MCPClientManager()
    manager.clients = {
        "good1": SlowInitClient(delay=0.01),
        "bad": SlowInitClient(delay=0.01, fail=True),
        "good2": SlowInitClient(delay=0.01),
    }

    proxy = MCPProxyServer(manager)
    await proxy.initialize_remote_clients()

    assert "bad" not in manager.clients
    assert "bad" not in proxy.capabilities
    assert "good1" in manager.clients and "good1" in proxy.capabilities
    assert "good2" in manager.clients and "good2" in proxy.capabilities


# ─────────────────────────────────────────────────────────────────────────────
# Improvement 1b: MultiMCP._connect_always_on_servers is concurrent
# ─────────────────────────────────────────────────────────────────────────────


def _make_multi_mcp_with_slow_manager(
    delay: float = DELAY, fail_servers: set = frozenset()
) -> tuple[MultiMCP, list[str]]:
    """MultiMCP whose client manager takes `delay` seconds per connection."""
    mm = MultiMCP()
    connected: list[str] = []

    async def slow_get_or_create(name: str):
        await asyncio.sleep(delay)
        if name in fail_servers:
            raise ConnectionError(f"{name} unreachable")
        connected.append(name)
        return MagicMock()

    mm.client_manager.get_or_create_client = slow_get_or_create
    mm.proxy = MagicMock()
    mm.proxy.initialize_single_client = AsyncMock()
    mm.proxy._send_tools_list_changed = AsyncMock()
    return mm, connected


def _yaml_config_with_servers(n: int, always_on: bool) -> MultiMCPConfig:
    return MultiMCPConfig(
        servers={
            f"srv{i}": ServerConfig(command="echo", always_on=always_on)
            for i in range(n)
        }
    )


@pytest.mark.asyncio
async def test_connect_always_on_servers_concurrent():
    """Always-on servers must connect in ~max(delay), not sum(delays)."""
    mm, connected = _make_multi_mcp_with_slow_manager()
    config = _yaml_config_with_servers(N_SERVERS, always_on=True)

    start = time.monotonic()
    await mm._connect_always_on_servers(config)
    elapsed = time.monotonic() - start

    assert elapsed < PARALLEL_CEILING, (
        f"_connect_always_on_servers took {elapsed:.2f}s — "
        f"must beat the {SEQUENTIAL_FLOOR}s sequential floor"
    )
    assert sorted(connected) == [f"srv{i}" for i in range(N_SERVERS)]
    assert mm.proxy.initialize_single_client.await_count == N_SERVERS


@pytest.mark.asyncio
async def test_connect_always_on_skips_lazy_servers():
    """Regression guard: only always_on servers are connected."""
    mm, connected = _make_multi_mcp_with_slow_manager(delay=0.01)
    config = MultiMCPConfig(
        servers={
            "eager": ServerConfig(command="echo", always_on=True),
            "lazy": ServerConfig(command="echo", always_on=False),
        }
    )
    await mm._connect_always_on_servers(config)
    assert connected == ["eager"]


@pytest.mark.asyncio
async def test_connect_always_on_failure_isolated():
    """Regression guard: one failing server doesn't block the others."""
    mm, connected = _make_multi_mcp_with_slow_manager(
        delay=0.01, fail_servers={"srv1"}
    )
    config = _yaml_config_with_servers(3, always_on=True)

    await mm._connect_always_on_servers(config)  # must not raise

    assert sorted(connected) == ["srv0", "srv2"]
    assert mm.proxy.initialize_single_client.await_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Improvement 1c: MultiMCP._connect_json_config_servers is concurrent
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_json_config_servers_concurrent():
    """JSON --config eager init must connect in ~max(delay), not sum(delays)."""
    mm, connected = _make_multi_mcp_with_slow_manager()
    config = _yaml_config_with_servers(N_SERVERS, always_on=False)

    start = time.monotonic()
    await mm._connect_json_config_servers(config)
    elapsed = time.monotonic() - start

    assert elapsed < PARALLEL_CEILING, (
        f"_connect_json_config_servers took {elapsed:.2f}s — "
        f"must beat the {SEQUENTIAL_FLOOR}s sequential floor"
    )
    assert sorted(connected) == [f"srv{i}" for i in range(N_SERVERS)]
    assert mm.proxy.initialize_single_client.await_count == N_SERVERS


@pytest.mark.asyncio
async def test_connect_json_config_servers_failure_isolated():
    """Regression guard: one failing server doesn't abort JSON-config init."""
    mm, connected = _make_multi_mcp_with_slow_manager(
        delay=0.01, fail_servers={"srv0"}
    )
    config = _yaml_config_with_servers(3, always_on=False)

    await mm._connect_json_config_servers(config)  # must not raise

    assert sorted(connected) == ["srv1", "srv2"]


# ─────────────────────────────────────────────────────────────────────────────
# Improvement 2: per-server tool_call_timeout_seconds
# ─────────────────────────────────────────────────────────────────────────────


def _make_proxy_with_tool(
    server_name: str = "srv",
    tool_name: str = "echo",
    client=None,
    server_config: dict = None,
) -> MCPProxyServer:
    manager = MCPClientManager()
    if server_config is not None:
        manager.server_configs[server_name] = server_config
    proxy = MCPProxyServer(manager)
    proxy.trigger_manager = AsyncMock()
    proxy.trigger_manager.check_and_enable = AsyncMock(return_value=[])
    key = f"{server_name}__{tool_name}"
    tool = types.Tool(
        name=key,
        description="t",
        inputSchema={"type": "object", "properties": {}},
    )
    proxy.tool_to_server[key] = ToolMapping(
        server_name=server_name, client=client, tool=tool
    )
    return proxy


def _call_req(key: str, arguments: dict = None) -> types.CallToolRequest:
    return types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=key, arguments=arguments or {}),
    )


@pytest.mark.asyncio
async def test_tool_call_timeout_wired_when_configured():
    """When tool_call_timeout_seconds is set, call_tool gets read_timeout_seconds."""
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value=types.CallToolResult(content=[])
    )
    proxy = _make_proxy_with_tool(
        client=mock_client,
        server_config={"command": "echo", "tool_call_timeout_seconds": 7},
    )

    result = await proxy._call_tool(_call_req("srv__echo"))

    assert not getattr(result.root, "isError", False)
    _, kwargs = mock_client.call_tool.call_args
    assert kwargs.get("read_timeout_seconds") == timedelta(seconds=7)


@pytest.mark.asyncio
async def test_tool_call_no_timeout_by_default():
    """No config → call_tool invoked WITHOUT read_timeout_seconds (previous behavior)."""
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value=types.CallToolResult(content=[])
    )
    proxy = _make_proxy_with_tool(client=mock_client, server_config={"command": "echo"})

    await proxy._call_tool(_call_req("srv__echo"))

    _, kwargs = mock_client.call_tool.call_args
    assert "read_timeout_seconds" not in kwargs


def test_get_tool_call_timeout_resolution():
    """Helper returns the timeout only for valid positive numeric configs."""
    manager = MCPClientManager()
    manager.server_configs = {
        "valid": {"tool_call_timeout_seconds": 12.5},
        "valid_int": {"tool_call_timeout_seconds": 30},
        "zero": {"tool_call_timeout_seconds": 0},
        "negative": {"tool_call_timeout_seconds": -5},
        "wrong_type": {"tool_call_timeout_seconds": "abc"},
        "unset": {"command": "echo"},
    }
    proxy = MCPProxyServer(manager)

    assert proxy._get_tool_call_timeout("valid") == 12.5
    assert proxy._get_tool_call_timeout("valid_int") == 30.0
    assert proxy._get_tool_call_timeout("zero") is None
    assert proxy._get_tool_call_timeout("negative") is None
    assert proxy._get_tool_call_timeout("wrong_type") is None
    assert proxy._get_tool_call_timeout("unset") is None
    assert proxy._get_tool_call_timeout("unknown_server") is None


@pytest.mark.asyncio
async def test_slow_backend_times_out_e2e():
    """E2E proof: a hung backend returns an isError timeout response quickly
    (instead of stalling forever) and the failure counts toward the circuit
    breaker. Uses a real in-memory MCP ClientSession so the SDK's
    read_timeout_seconds enforcement is exercised for real."""
    server = Server("slow")

    @server.list_tools()
    async def _():
        return [
            types.Tool(
                name="hang",
                description="never returns in time",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    @server.call_tool()
    async def _(tool_name, params):
        await asyncio.sleep(5.0)  # far beyond the timeout
        return []

    async with create_connected_server_and_client_session(server) as client:
        manager = MCPClientManager()
        manager.clients = {"slow": client}
        manager.server_configs["slow"] = {
            "command": "echo",
            "tool_call_timeout_seconds": 0.3,
        }
        proxy = await MCPProxyServer.create(manager)

        start = time.monotonic()
        result = await proxy._call_tool(_call_req("slow__hang"))
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, (
            f"tool call took {elapsed:.2f}s — timeout of 0.3s was not enforced"
        )
        assert result.root.isError is True
        # Timeout failure feeds the circuit breaker (repeated hangs quarantine)
        assert proxy._tool_failure_counts.get("slow__hang") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Config plumbing for tool_call_timeout_seconds
# ─────────────────────────────────────────────────────────────────────────────


def test_server_config_timeout_field_validation():
    """Field defaults to None, accepts positive numbers, rejects <= 0."""
    assert ServerConfig(command="echo").tool_call_timeout_seconds is None
    assert ServerConfig(
        command="echo", tool_call_timeout_seconds=30
    ).tool_call_timeout_seconds == 30.0

    with pytest.raises(ValidationError):
        ServerConfig(command="echo", tool_call_timeout_seconds=0)
    with pytest.raises(ValidationError):
        ServerConfig(command="echo", tool_call_timeout_seconds=-1)

    # exclude_none keeps unset timeout out of server dicts (previous shape kept)
    assert "tool_call_timeout_seconds" not in ServerConfig(
        command="echo"
    ).model_dump(exclude_none=True)
    assert (
        ServerConfig(command="echo", tool_call_timeout_seconds=15).model_dump(
            exclude_none=True
        )["tool_call_timeout_seconds"]
        == 15.0
    )


def test_server_config_timeout_yaml_roundtrip(tmp_path):
    """tool_call_timeout_seconds survives save_config/load_config."""
    path = tmp_path / "servers.yaml"
    config = MultiMCPConfig(
        servers={
            "svc": ServerConfig(command="echo", tool_call_timeout_seconds=45),
            "other": ServerConfig(command="echo"),
        }
    )
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.servers["svc"].tool_call_timeout_seconds == 45.0
    assert loaded.servers["other"].tool_call_timeout_seconds is None


@pytest.mark.asyncio
async def test_create_clients_eager_stores_server_configs():
    """Eager create_clients records server_configs (needed for timeout lookup
    and consistent with the lazy add_pending_server path)."""
    manager = MCPClientManager()
    manager._create_single_client = AsyncMock()

    await manager.create_clients(
        {"mcpServers": {"svc": {"command": "echo", "tool_call_timeout_seconds": 9}}}
    )

    assert manager.server_configs["svc"]["tool_call_timeout_seconds"] == 9
    await manager.close()
