# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-MCP is a Python-based proxy server that acts as a single MCP (Model Context Protocol) server while connecting to and routing between multiple backend MCP servers. It supports both STDIO and SSE (Server-Sent Events) transports and can dynamically add/remove MCP servers at runtime.

## Architecture

### Core Components

- **MultiMCP (`src/multimcp/multi_mcp.py`)**: Main server orchestrator that handles configuration, transport modes, and HTTP endpoints
- **MCPProxyServer (`src/multimcp/mcp_proxy.py`)**: Core proxy that forwards MCP requests to backend servers, handles tool namespacing (`server_name_tool_name`), and manages capabilities aggregation
- **MCPClientManager (`src/multimcp/mcp_client.py`)**: Manages lifecycle of multiple MCP client connections (both stdio and SSE)
- **Logger (`src/utils/logger.py`)**: Centralized logging using loguru with `multi_mcp.*` namespace

### Key Patterns

- **Tool Namespacing**: Tools are namespaced as `server_name__tool_name` (double underscore separator) to avoid conflicts when multiple servers expose tools with the same name
- **Transport Flexibility**: Supports both STDIO (for CLI/pipe-based) and SSE (for HTTP/network-based) communication
- **Dynamic Server Management**: Can add/remove MCP servers at runtime via HTTP API (SSE mode only)
- **Capability Aggregation**: Proxies and combines tools, prompts, and resources from all connected backend servers.

## Development Commands

### Running the Server
```bash
# STDIO mode (default)
uv run main.py --transport stdio

# SSE mode with custom host/port
uv run main.py --transport sse --host 0.0.0.0 --port 8080

# With custom config file
uv run main.py --config ./examples/config/mcp_k8s.json

# Quick development run
make run
```

### Testing
```bash
# Individual test suites
make test-proxy      # Core proxy functionality  
make test-e2e        # End-to-end integration tests
make test-lifecycle  # Client lifecycle management
make test-k8s        # Kubernetes deployment tests (requires docker-build)

# All tests
make all-test

# Run specific test file directly
pytest -s tests/proxy_test.py
```

### Docker & Kubernetes
```bash
# Build and run locally
make docker-build
make docker-run

# Kubernetes with Kind
kind create cluster --name multi-mcp-test
kind load docker-image multi-mcp --name multi-mcp-test
kubectl apply -f examples/k8s/multi-mcp.yaml
```

### Docker Container Details
- **Base Image**: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (Debian-based)
- **Node.js Runtime**: Node.js 20.x installed for MCP servers requiring Node runtime
- **Production Config**: Uses `./msc/mcp.json` with GitHub, Brave Search, and Context7 MCP servers
- **Network**: Binds to `0.0.0.0` for container accessibility

### Dependency Management
```bash
# Install dependencies
uv venv
uv pip install -r requirements.txt

# Alternative: Direct run (handles dependencies automatically)
uv run main.py
```

## Configuration

### MCP Server Configuration
Configuration is JSON-based, defining backend MCP servers to connect to:

```json
{
  "mcpServers": {
    "weather": {
      "command": "python",
      "args": ["./tools/get_weather.py"],
      "env": {"API_KEY": "value"}
    },
    "remote_service": {
      "url": "http://127.0.0.1:9080/sse"
    }
  }
}
```

### Runtime HTTP API (SSE Mode Only)
- `GET /mcp_servers` - List active servers
- `POST /mcp_servers` - Add new servers
- `DELETE /mcp_servers/{name}` - Remove server
- `GET /mcp_tools` - List all tools by server

## Code Conventions

### Error Handling
- Use Rich logging with emoji prefixes: `✅ success`, `❌ errors`, `⚠️ warnings`
- Log errors but don't raise exceptions for individual server failures
- Gracefully handle missing tools/prompts/resources with appropriate error responses
- **Capability Checks**: Always verify server capabilities before calling MCP methods to avoid "Method not found" errors

### Async Patterns
- Use `AsyncExitStack` for managing multiple client lifecycles
- Prefer `async with` for resource management
- Always await client operations and handle exceptions per-client

### Type Hints
- Use `from typing import` imports for type annotations
- Prefer `Optional[Type]` over `Type | None` for Python 3.10 compatibility
- Use `Literal` for string enums (transport modes, log levels)

## Development Notes

### Tool Namespacing Implementation
Tools are internally namespaced using `_make_key()` and `_split_key()` static methods in `MCPProxyServer`:
- `_make_key(server_name, tool_name)` creates `server_name__tool_name` identifiers (double underscore `__` separator)
- `_split_key(key)` splits namespaced keys back into `(server, tool)` tuples using the `__` separator
- Namespaced tools are stored in `tool_to_server` dict mapping keys to `ToolMapping` objects
- This allows multiple servers to expose tools with identical base names without conflicts

### Capability Management
- Server capabilities are checked during initialization and stored in `self.capabilities[name]`
- `_list_prompts()` and `_list_resources()` serve from cache populated during server registration
- Prevents "Method not found" errors when servers don't implement all MCP methods
- Graceful handling of servers with different capability sets (tools-only vs full MCP servers)

### Transport Modes
- **STDIO**: Pipe-based communication for CLI tools and local agents
- **SSE**: HTTP Server-Sent Events for browser agents and network access

### Testing Strategy
- **Unit tests**: Focus on individual components (proxy, client manager)
- **Integration tests**: End-to-end flows with real MCP tools
- **K8s tests**: Deployment and service exposure validation

### Project Structure
- `main.py` - Entry point CLI interface using `MultiMCP` class
- `src/multimcp/multi_mcp.py` - Main orchestrator with `MCPSettings` and HTTP endpoints
- `src/multimcp/mcp_proxy.py` - Core proxy server with request forwarding and capability aggregation
- `src/multimcp/mcp_client.py` - Client lifecycle management using `AsyncExitStack`
- `src/utils/logger.py` - Centralized Rich logging with `multi_mcp.*` namespace
- `tests/` - Comprehensive test suite with mock MCP servers and fixtures
- `examples/config/` - Sample configuration files for different deployment scenarios

### Dependencies
Key dependencies include:
- `mcp>=1.4.1` - Core MCP protocol implementation
- `langchain-mcp-adapters` - LangChain integration utilities  
- `starlette` + `uvicorn` - SSE HTTP server
- `httpx-sse` - SSE client support
- `rich` - Enhanced logging and console output
- `pytest` + `pytest-asyncio` - Testing framework with async support

## MCP Server Status & Testing

### Configured MCP Servers (msc/mcp.json)

**GitHub MCP Server** ✅ **WORKING**
- **Server**: `github` (Node.js via npx @modelcontextprotocol/server-github)
- **Tool Prefix**: `mcp__multi-mcp__github_*`
- **Test Action**: `github_search_repositories` - Successfully searched repositories
- **Capabilities**: Repository management, issues, pull requests, file operations
- **Authentication**: Uses GITHUB_PERSONAL_ACCESS_TOKEN environment variable

**Brave Search MCP Server** ✅ **WORKING**  
- **Server**: `brave-search` (Node.js via npx @modelcontextprotocol/server-brave-search)
- **Tool Prefix**: `mcp__multi-mcp__brave-search_*`
- **Test Action**: `brave_web_search` - Successfully performed web search
- **Capabilities**: Web search, local business search
- **Authentication**: Uses BRAVE_API_KEY environment variable

**Context7 MCP Server** ✅ **WORKING**
- **Server**: `context7` (Node.js via npx @upstash/context7-mcp)
- **Tool Prefix**: `mcp__multi-mcp__context7_*`
- **Test Actions**: `resolve-library-id` + `get-library-docs` - Successfully retrieved React documentation
- **Capabilities**: Library documentation lookup, code examples, version-specific docs
- **Authentication**: None required (public documentation service)

### Tool Namespacing in Action
All MCP tools are accessible through the multi-mcp proxy with the naming pattern:
`mcp__multi-mcp__{server_name}_{tool_name}`

**Example tested tools:**
- `mcp__multi-mcp__github_search_repositories`
- `mcp__multi-mcp__brave-search_brave_web_search`  
- `mcp__multi-mcp__context7_resolve-library-id`
- `mcp__multi-mcp__context7_get-library-docs`

### Verification Status
- **Last Tested**: 2025-06-27
- **Test Environment**: Claude Code via multi-mcp proxy
- **All Servers**: ✅ Functional and responsive
- **Tool Discovery**: All namespaced tools properly exposed through proxy
- **Error Handling**: Graceful fallback for server-specific failures

## 🚨 CRITICAL Git Workflow Rules

### Mandatory File Creation Workflow
**NEVER** create files remotely using GitHub MCP tools or API. **ALWAYS** follow this exact sequence:

1. **Create Locally**: Use `Write` tool to create files in local filesystem
2. **Stage**: `git add <file>` to stage changes
3. **Commit**: `git commit -m "message"` to commit locally
4. **Push**: `git push` to sync with remote

### Absolutely Forbidden Operations
❌ **NEVER** use `mcp__multi-mcp__github_create_or_update_file` for new files  
❌ **NEVER** create files directly on remote repository  
❌ **NEVER** bypass local Git workflow  
❌ **NEVER** create divergence between local and remote  

### Why This Matters
- **Repository Integrity**: Maintains consistent Git history
- **Collaboration**: Ensures all changes go through proper review process
- **Conflict Prevention**: Avoids merge conflicts and divergence
- **Workflow Compliance**: Follows standard Git practices

### Emergency Recovery from Divergence
If divergence occurs:
1. `git stash` (save local changes)
2. `git pull` (sync with remote)
3. `git stash pop` (restore local changes)
4. Resolve conflicts and follow proper workflow

### Investigation Storage
- **Location**: `claude/{investigation_id}/` directory structure
- **Naming**: Use timestamp-based IDs for unique identification
- **Example**: `claude/250627114051/investigation-file.md`

### File Organization
- **Investigations**: Store in `claude/{id}/` directories
- **Documentation**: Keep in root or `docs/` as appropriate
- **Configurations**: Use existing `examples/` structure

<!-- GSD:project-start source:PROJECT.md -->
## Project

**Multi-MCP Phase 2: Versioned Roots-Anchored BMXF Routing**

A code-grounded implementation of roots-anchored BMXF tool retrieval for the Multi-MCP proxy server. The system replaces the current `PassthroughRetriever` (returns all tools) with a context-aware retrieval pipeline that:

1. At session init, scans declared MCP roots to fingerprint the workspace
2. Uses BMX (entropy-weighted BM25 successor) with field weighting (BMXF) to score and rank tools
3. Exposes a bounded active set (K=15–20) with remaining tools accessible via a routing tool
4. Adapts turn-by-turn using blended RRF fusion of environment and conversation signals

**Core Value:** **Turn-zero tool relevance without full-catalog exposure.** Every session starts with the right tools visible, not all 168 tools dumped into the model's context window.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:STACK.md -->
## Technology Stack

See **Dependencies** section above for the complete technology stack. Key technologies: Python 3.10+, asyncio/anyio, Starlette + Uvicorn, MCP SDK, loguru, Pydantic v2, httpx.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

See **Code Conventions** section above for established patterns (error handling, async patterns, type hints, tool namespacing).
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

See **Architecture** section above for core components (MultiMCP, MCPProxyServer, MCPClientManager) and key patterns (tool namespacing, transport flexibility, dynamic server management).
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
