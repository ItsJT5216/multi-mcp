"""Live subprocess tests — real multi-mcp proxy over real HTTP and SSE.

Scenario A: server add/remove, tool discovery, tool execution.
Scenario B: bounded-set exposure, live request_tool proxy execution.

Both scenarios start a real multi-mcp subprocess, wait for it to be healthy,
then exercise the full stack over real HTTP and real SSE connections.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

REPO_ROOT = Path(__file__).parent.parent


def _find_free_port() -> int:
    """Find a free ephemeral port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


CONFIG_A = str(REPO_ROOT / "examples" / "config" / "mcp.json")
RETRIEVAL_HELPER = str(REPO_ROOT / "tests" / "tools" / "retrieval_server.py")


# ─── helpers ──────────────────────────────────────────────────────────────────


async def _wait_for_health(port: int, timeout: float = 20.0, interval: float = 0.3) -> bool:
    """Poll /health until 200 or timeout."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
                if r.status_code == 200:
                    return True
            except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException):
                # Continue retrying on connection/request errors
                pass
            await asyncio.sleep(interval)
    return False


async def _mcp_list_tools(port: int) -> list[Any]:
    """Open an SSE session, initialize, and return the tools list."""
    async with sse_client(f"http://127.0.0.1:{port}/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return list(result.tools)


async def _mcp_call_tool(port: int, tool_name: str, args: dict) -> Any:
    """Open an SSE session and call one tool, returning the raw result."""
    async with sse_client(f"http://127.0.0.1:{port}/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, args)


# ─── Scenario A ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def scenario_a_proc():
    """Start a real multi-mcp SSE subprocess on a dynamically allocated port."""
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(REPO_ROOT / "main.py"),
            "start",
            "--transport", "sse",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--config", CONFIG_A,
        ],
        cwd=str(REPO_ROOT),
    )
    yield proc, port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="module")
async def scenario_a(scenario_a_proc):
    """Wait for the Scenario A server to be healthy before running tests."""
    proc, port = scenario_a_proc
    ready = await _wait_for_health(port, timeout=20.0)
    assert ready, (
        f"Scenario A server on port {port} did not become healthy within 20s. "
        "Check the subprocess logs for details."
    )
    return port


class TestScenarioA:
    """Real server add/remove, tool discovery, tool execution via live SSE proxy."""

    @pytest.mark.anyio
    async def test_a1_health_endpoint(self, scenario_a):
        """Server /health returns 200 with status information."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{scenario_a}/health", timeout=5.0)
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") in ("ok", "healthy", "ready", "available"), (
            f"Unexpected health status: {body}"
        )

    @pytest.mark.anyio
    async def test_a2_initial_servers_listed(self, scenario_a):
        """GET /mcp_servers lists the initially configured servers."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{scenario_a}/mcp_servers", timeout=5.0)
        assert r.status_code == 200
        body = r.json()
        # Either active or pending — both configurations are valid at this point
        all_servers = set(body.get("active_servers", [])) | set(body.get("pending_servers", []))
        # calculator must be present (from mcp.json)
        assert "calculator" in all_servers, (
            f"Expected 'calculator' in servers, got: {body}"
        )

    @pytest.mark.anyio
    async def test_a3_tool_discovery_via_http(self, scenario_a):
        """GET /mcp_tools returns calculator tools by server."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{scenario_a}/mcp_tools", timeout=5.0)
        assert r.status_code == 200
        body = r.json()
        tools = body.get("tools", {})
        # calculator server must have add and multiply
        assert "calculator" in tools, f"Expected 'calculator' in tools, got keys: {list(tools.keys())}"
        calc_tools = tools["calculator"]
        assert "add" in calc_tools, f"Expected 'add' in calculator tools: {calc_tools}"
        assert "multiply" in calc_tools, f"Expected 'multiply' in calculator tools: {calc_tools}"

    @pytest.mark.anyio
    async def test_a4_tool_execution_via_sse(self, scenario_a):
        """Call calculator__add through a real SSE session and verify the result."""
        result = await _mcp_call_tool(scenario_a, "calculator__add", {"a": 11, "b": 22})
        assert not result.isError, f"Tool call returned error: {result}"
        output = result.content[0].text if result.content else ""
        assert "33" in str(output), f"Expected 33 in result, got: {output!r}"

    @pytest.mark.anyio
    async def test_a5_tool_list_via_sse(self, scenario_a):
        """tools/list over SSE returns namespaced calculator tools."""
        tools = await _mcp_list_tools(scenario_a)
        names = [t.name for t in tools]
        assert "calculator__add" in names, f"Expected calculator__add in: {names}"
        assert "calculator__multiply" in names, f"Expected calculator__multiply in: {names}"

    @pytest.mark.anyio
    async def test_a6_dynamic_server_lifecycle(self, scenario_a):
        """POST /mcp_servers adds unit_convertor, call it via SSE, then DELETE removes it."""
        # Step 1: Add unit_convertor server
        payload = {
            "mcpServers": {
                "unit_convertor": {
                    "command": sys.executable,
                    "args": ["./tests/tools/unit_convertor.py"],
                }
            }
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"http://127.0.0.1:{scenario_a}/mcp_servers",
                json=payload,
                timeout=15.0,
            )
        assert r.status_code == 200, f"POST /mcp_servers failed: {r.status_code} {r.text}"
        body = r.json()
        assert "unit_convertor" in str(body.get("message", "")), (
            f"Expected 'unit_convertor' in response message: {body}"
        )

        # Verify tools appear in /mcp_tools
        async with httpx.AsyncClient() as client:
            r2 = await client.get(f"http://127.0.0.1:{scenario_a}/mcp_tools", timeout=5.0)
        tools = r2.json().get("tools", {})
        assert "unit_convertor" in tools, (
            f"Expected 'unit_convertor' in tools after add, got: {list(tools.keys())}"
        )

        # Step 2: Execute the added tool via SSE
        result = await _mcp_call_tool(
            scenario_a,
            "unit_convertor__convert_length",
            {"value": 1.0, "from_unit": "kilometers", "to_unit": "miles"},
        )
        assert not result.isError, f"Tool call returned error: {result}"
        output = result.content[0].text if result.content else ""
        # 1 km ≈ 0.621371 miles
        assert "0.621" in str(output), f"Expected ~0.621 in result, got: {output!r}"

        # Step 3: Remove the server
        async with httpx.AsyncClient() as client:
            r3 = await client.delete(
                f"http://127.0.0.1:{scenario_a}/mcp_servers/unit_convertor",
                timeout=10.0,
            )
        assert r3.status_code == 200, f"DELETE /mcp_servers/unit_convertor failed: {r3.status_code} {r3.text}"

        # Verify tools are gone from /mcp_tools
        async with httpx.AsyncClient() as client:
            r4 = await client.get(f"http://127.0.0.1:{scenario_a}/mcp_tools", timeout=5.0)
        tools_after = r4.json().get("tools", {})
        assert "unit_convertor" not in tools_after, (
            f"Expected 'unit_convertor' removed from tools, still present: {list(tools_after.keys())}"
        )


# ─── Scenario B ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def scenario_b_proc():
    """Write a temp JSON with calculator + retrieval settings, start retrieval_server.py.

    Passing config= to MultiMCP prevents auto-discovery of system-wide MCP servers,
    keeping this scenario isolated to the calculator server only.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="live_retrieval_",
        encoding="utf-8",
    )
    json_config = {
        "mcpServers": {
            "calculator": {
                "command": sys.executable,
                "args": ["./tests/tools/calculator.py"],
                "always_on": True,
            }
        },
        "retrieval": {
            "enabled": True,
            "rollout_stage": "ga",
            "shadow_mode": False,
            "scorer": "keyword",
            "top_k": 1,
            "max_k": 1,
            "enable_routing_tool": True,
        },
    }
    json.dump(json_config, tmp)
    tmp.flush()
    json_path = tmp.name
    tmp.close()

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    port = _find_free_port()
    proc = subprocess.Popen(
        [sys.executable, RETRIEVAL_HELPER, json_path, str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    yield proc, port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    Path(json_path).unlink(missing_ok=True)


@pytest.fixture(scope="module")
async def scenario_b(scenario_b_proc):
    """Wait for the Scenario B server to be healthy + always_on server to connect."""
    proc, port = scenario_b_proc
    ready = await _wait_for_health(port, timeout=20.0)
    assert ready, (
        f"Scenario B server on port {port} did not become healthy within 20s. "
        "Check the subprocess logs for details."
    )

    # Poll /mcp_tools until calculator tools and request_tool are registered
    deadline = time.monotonic() + 20.0
    tools_ready = False
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"http://127.0.0.1:{port}/mcp_tools", timeout=2.0)
                if r.status_code == 200:
                    tools = r.json().get("tools", {})
                    # Check for calculator tools (always_on server)
                    if "calculator" in tools:
                        calc_tools = tools["calculator"]
                        if any(t in calc_tools for t in ["add", "multiply"]):
                            tools_ready = True
                            break
            except (httpx.RequestError, httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(0.3)

    assert tools_ready, (
        f"Calculator tools did not appear in /mcp_tools within 20s. "
        "Check the subprocess logs for details."
    )
    return port


class TestScenarioB:
    """Real bounded-set exposure and live request_tool proxy execution."""

    @pytest.mark.anyio
    async def test_b1_tool_list_bounded_to_max_k(self, scenario_b):
        """With retrieval enabled + GA + max_k=1, tools/list returns ≤2 tools (direct + request_tool)."""
        tools = await _mcp_list_tools(scenario_b)
        names = [t.name for t in tools]
        # Direct tools are bounded to max_k=1; request_tool is additive
        direct_tools = [n for n in names if n != "request_tool"]
        assert len(direct_tools) <= 1, (
            f"Expected ≤1 direct tool with max_k=1, got {len(direct_tools)}: {direct_tools}"
        )
        assert len(tools) <= 2, (
            f"Expected ≤2 total tools (direct + request_tool), got {len(tools)}: {names}"
        )

    @pytest.mark.anyio
    async def test_b2_request_tool_present(self, scenario_b):
        """request_tool is present in the tool list when demoted tools exist."""
        tools = await _mcp_list_tools(scenario_b)
        names = [t.name for t in tools]
        assert "request_tool" in names, (
            f"Expected 'request_tool' in tool list (demoted tools should exist): {names}"
        )

    @pytest.mark.anyio
    async def test_b3_request_tool_enum_contains_demoted_tool(self, scenario_b):
        """request_tool inputSchema enum lists the demoted calculator tool."""
        tools = await _mcp_list_tools(scenario_b)
        routing_tool = next((t for t in tools if t.name == "request_tool"), None)
        assert routing_tool is not None, "request_tool must be present"

        schema = routing_tool.inputSchema
        name_enum = schema.get("properties", {}).get("name", {}).get("enum", [])
        assert len(name_enum) > 0, (
            f"request_tool enum must have at least one demoted tool, got: {schema}"
        )
        # The enum should contain calculator tools (whichever was demoted)
        calc_tools = [e for e in name_enum if e.startswith("calculator__")]
        assert len(calc_tools) > 0, (
            f"Expected a calculator tool in request_tool enum: {name_enum}"
        )

    @pytest.mark.anyio
    async def test_b4_request_tool_describe_returns_schema(self, scenario_b):
        """Calling request_tool with describe=True returns the demoted tool's JSON schema."""
        tools = await _mcp_list_tools(scenario_b)
        routing_tool = next((t for t in tools if t.name == "request_tool"), None)
        assert routing_tool is not None, "request_tool must be present"

        # Find a demoted tool from the enum
        name_enum = routing_tool.inputSchema.get("properties", {}).get("name", {}).get("enum", [])
        assert name_enum, "request_tool enum must not be empty"
        demoted_tool = name_enum[0]

        result = await _mcp_call_tool(
            scenario_b,
            "request_tool",
            {"name": demoted_tool, "describe": True},
        )
        assert not result.isError, f"request_tool(describe=True) returned error: {result}"
        output = result.content[0].text if result.content else ""

        # Output should be a JSON schema object
        schema_data = json.loads(output)
        assert "name" in schema_data, f"Schema must have 'name' field: {schema_data}"
        assert "inputSchema" in schema_data, f"Schema must have 'inputSchema': {schema_data}"

    @pytest.mark.anyio
    async def test_b5_request_tool_proxy_executes_demoted_tool(self, scenario_b):
        """Calling request_tool with describe=False proxies through to the actual tool."""
        tools = await _mcp_list_tools(scenario_b)
        routing_tool = next((t for t in tools if t.name == "request_tool"), None)
        assert routing_tool is not None, "request_tool must be present"

        name_enum = routing_tool.inputSchema.get("properties", {}).get("name", {}).get("enum", [])
        assert name_enum, "request_tool enum must not be empty"
        demoted_tool = name_enum[0]

        # Determine correct arguments based on which calculator tool was demoted
        if "multiply" in demoted_tool:
            call_args = {"a": 3, "b": 4}
            expected = "12"
        else:
            # add
            call_args = {"a": 10, "b": 5}
            expected = "15"

        result = await _mcp_call_tool(
            scenario_b,
            "request_tool",
            {"name": demoted_tool, "describe": False, "arguments": call_args},
        )
        assert not result.isError, f"request_tool proxy call returned error: {result}"
        output = result.content[0].text if result.content else ""
        assert expected in str(output), (
            f"Expected {expected!r} in proxy result for {demoted_tool}({call_args}), got: {output!r}"
        )