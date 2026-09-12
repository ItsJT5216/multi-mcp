#!/usr/bin/env python3
"""Test helper: starts multi-mcp SSE server with a custom JSON config path.

The JSON config may include both "mcpServers" and a "retrieval" section.
Passing config= to MultiMCP prevents auto-discovery of system-wide MCP servers.

Usage:
    python tests/tools/retrieval_server.py <json_path> [port]
"""
import asyncio
import sys
from pathlib import Path

def main() -> None:
    """Parse CLI args and start the MultiMCP SSE server with the given JSON config."""
    if len(sys.argv) < 2:
        print("Usage: retrieval_server.py <json_path> [port]", file=sys.stderr)
        sys.exit(1)

    json_path = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8087

    if not json_path.exists():
        print(f"ERROR: JSON config not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    from src.multimcp.multi_mcp import MultiMCP
    server = MultiMCP(transport="sse", host="127.0.0.1", port=port, config=str(json_path))
    asyncio.run(server.run())

if __name__ == "__main__":
    main()
