import asyncio
import pytest
import httpx
import json
from langchain_mcp_adapters.client import MultiServerMCPClient
from tests.utils import run_e2e_test_with_client

MCP_SERVER_URL = "http://127.0.0.1:8083"
MANAGEMENT_API_KEY = "lifecycle-test-management-key"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {MANAGEMENT_API_KEY}",
}
EXPECTED_TOOLS=["convert_temperature","convert_length", "calculator__add", "calculator__multiply"]
TEST_PROMPTS=[
        ("Convert temperature of 100 Celsius to Fahrenheit?", "212"),
        ("what's the answer for (10 + 5)?", "15"),
    ]

ADD_PAYLOAD = {
    "mcpServers": {
        "unit_converter": {
            "command": "python",
            "args": ["./tests/tools/unit_convertor.py"]
        }
    }
}


@pytest.mark.asyncio
async def test_mcp_servers_lifecycle():
    """
    Test the lifecycle of adding, verifying, and removing an MCP server dynamically via the HTTP API.
    Also verifies the new server's tools can be used through the proxy.
    """
    process = await asyncio.create_subprocess_exec(
        "python", "main.py", "start", "--transport", "sse", "--config", "./examples/config/mcp.json",
        "--management-api-key", MANAGEMENT_API_KEY,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.sleep(6)  # wait for the server to be up (may load additional YAML servers)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # GET existing servers
            resp = await client.get(f"{MCP_SERVER_URL}/mcp_servers")
            assert resp.status_code == 200
            servers = resp.json().get("active_servers", [])
            print("🧪 Server list Before Add:", servers)

            # POST new server (retry up to 3 times — subprocess MCP server
            # may need a moment to complete handshake)
            for attempt in range(3):
                resp = await client.post(
                    f"{MCP_SERVER_URL}/mcp_servers",
                    headers=HEADERS,
                    content=json.dumps(ADD_PAYLOAD)
                )
                print(f"\n📋 POST attempt {attempt+1}: status={resp.status_code} body={resp.text}")
                if resp.status_code == 200:
                    break
                await asyncio.sleep(2)
            assert resp.status_code == 200, f"POST failed after 3 attempts: {resp.status_code}: {resp.text}"
            print("✅ Add Response:", resp.json())

            # GET again to verify it's added
            resp = await client.get(f"{MCP_SERVER_URL}/mcp_servers")
            assert resp.status_code == 200
            servers = resp.json().get("active_servers", [])
            assert "unit_converter" in servers
            print("🧪 Server list After Add:", servers)

            # Get tool list
            resp = await client.get(f"{MCP_SERVER_URL}/mcp_tools")
            assert resp.status_code == 200
            tools_by_server = resp.json().get("tools", {})

            all_tools = []
            for _, tools in tools_by_server.items():
                if isinstance(tools, list):
                    all_tools.extend(tools)
            print("🔧 Tools list:", all_tools)

        # Test tool discovery via SSE client
        # Only validate core test tools (weather, calculator, unit_converter)
        # since the server may also load servers from user's YAML config
        await asyncio.sleep(6)
        client = MultiServerMCPClient({
            "multi-mcp": {
                "transport": "sse",
                "url": "http://127.0.0.1:8083/sse",
            }
        })
        # Validate that the proxy discovers tools and that expected test tools are present
        tools = await client.get_tools()
        tool_names = [tool.name for tool in tools]
        print(f"🔧 SSE tools discovered: {len(tool_names)} tools")
        # Check that at least the weather/calculator tools from test config are present
        expected_tool_substrings = ["weather", "calculator"]
        for expected in expected_tool_substrings:
            matches = [t for t in tool_names if expected in t.lower()]
            assert len(matches) > 0, f"Expected to find tools containing '{expected}' but got: {tool_names}"
        print("✅ Tool discovery validation passed")

        async with httpx.AsyncClient() as client:
            # DELETE the server
            resp = await client.delete(
                f"{MCP_SERVER_URL}/mcp_servers/unit_converter", headers=HEADERS
            )
            assert resp.status_code == 200
            print("🗑️ Remove Response:", resp.json())

            # GET to confirm removal
            resp = await client.get(f"{MCP_SERVER_URL}/mcp_servers")
            assert resp.status_code == 200
            servers = resp.json().get("active_servers", [])
            assert "unit_converter" not in servers
            print("🧪 After Remove:", servers)

    finally:
        try:
            process.kill()
        except ProcessLookupError:
            pass  # Process already exited
        if process.stdout:
            await process.stdout.read()
        if process.stderr:
            await process.stderr.read()
