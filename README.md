# multi-mcp

A production-ready MCP proxy server that aggregates multiple backend MCP servers into a single endpoint — with lazy loading, per-tool filtering, and a unified YAML config that doubles as a live control plane.

```
All your AI tools → multi-mcp → github, obsidian, exa, tavily, context7, ...
```

---

## Why

Most MCP setups require configuring every server individually in every tool (Claude Code, Codex, Cursor, etc.). Each server starts eagerly at boot. You have no easy way to disable specific tools from a server you otherwise want.

**multi-mcp** solves all three:

- **One endpoint** — configure it once, every tool connects to multi-mcp
- **Lazy loading** — servers only connect when a tool is actually called
- **Tool control** — flip `enabled: false` on any individual tool in a YAML file

---

## Features

- **Unified YAML config** — single file serves as cache, config, and control plane
- **Startup discovery** — connects to every server briefly at first run, caches tool lists, disconnects lazy servers
- **Lazy loading** — lazy servers reconnect on first tool call, auto-disconnect after idle timeout
- **Always-on servers** — stays connected permanently, auto-reconnects if dropped
- **Per-tool enable/disable** — expose exactly the tools you want from each server
- **Smart refresh** — re-discovers tools without overwriting your settings
- **Stale tool cleanup** — tools that disappear from a server and were disabled get pruned automatically
- **Supports all transports** — stdio, SSE, and Streamable HTTP (2025 spec)
- **Tool namespacing** — `server::tool_name` prevents conflicts across servers
- **Runtime HTTP API** — add/remove servers without restarting (SSE mode)
- **Audit logging** — JSONL log of every tool call
- **API key auth** — optional Bearer token for SSE mode

---

## Quick Start

**Requirements:** Python 3.10+, [uv](https://github.com/astral-sh/uv)

```bash
git clone https://github.com/itstanner5216/multi-mcp
cd multi-mcp
uv sync
```

**First run** — auto-discovers all your servers and writes `~/.config/multi-mcp/servers.yaml`:

```bash
uv run python main.py start
```

**Or refresh manually** to re-discover tools and update the YAML:

```bash
uv run python main.py refresh
```

---

## Config

On first run, multi-mcp creates `~/.config/multi-mcp/servers.yaml` by connecting to every server you've configured, fetching its tool list, then disconnecting. The resulting file looks like:

```yaml
servers:
  github:
    command: /path/to/run-github.sh
    always_on: true          # stays connected at all times
    idle_timeout_minutes: 5
    tools:
      search_repositories:
        enabled: true
      delete_repository:
        enabled: false        # hidden from all AI tools
      create_gist:
        enabled: false

  exa:
    url: https://mcp.exa.ai/mcp?tools=web_search_exa,get_code_context_exa
    always_on: false          # lazy: connects only when called
    idle_timeout_minutes: 5
    tool_call_timeout_seconds: 60   # optional: fail hung tool calls after 60s
    tools:
      web_search_exa:
        enabled: true
      linkedin_search_exa:
        enabled: false        # don't need this

  obsidian:
    command: /path/to/run-obsidian.sh
    always_on: true
    tools: {}                 # auto-populated on first run
```

**Tool control rules:**

| State | Behavior |
|-------|----------|
| `enabled: true` | Exposed to AI |
| `enabled: false` | Hidden — setting is never overwritten by refresh |
| Tool disappears from server | Marked `stale: true`, your setting preserved |
| `stale: true` + `enabled: false` | Cleaned up on next refresh |
| No `tools` key | All tools pass through (default) |

To disable a tool, just set `enabled: false` and save. Takes effect on next `multi-mcp start`.

**Per-server timeouts:** set `tool_call_timeout_seconds` on a server to bound how long a tool call may take. When unset (default), calls wait indefinitely. When set, a hung backend returns a timeout error instead of stalling the request, and repeated timeouts trip the proxy's circuit breaker (auto-quarantine after 3 consecutive failures).

---

## CLI

```bash
# Start the proxy (stdio mode — used by Claude Code, Codex, etc.)
uv run python main.py start

# Start in SSE mode (network accessible)
uv run python main.py start --transport sse --port 8085

# Re-discover tools from all servers, smart-merge into YAML
uv run python main.py refresh

# Re-discover tools from one server only
uv run python main.py refresh github

# Show server status and tool counts
uv run python main.py status

# List all tools with enabled/disabled status
uv run python main.py list

# Filter to one server
uv run python main.py list --server github

# Show only disabled tools
uv run python main.py list --disabled
```

---

## Connecting Your AI Tools

Once multi-mcp is running, replace all individual server entries in your tool configs with a single entry:

**Claude Code / Cursor / any JSON-based config:**
```json
{
  "mcpServers": {
    "multi-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "/path/to/multi-mcp", "python", "main.py", "start"]
    }
  }
}
```

**SSE mode (if running as a background service):**
```json
{
  "mcpServers": {
    "multi-mcp": {
      "type": "sse",
      "url": "http://localhost:8085/sse"
    }
  }
}
```

---

## Transport Support

multi-mcp connects to backend servers over any transport:

| Transport | Backend config | Notes |
|-----------|---------------|-------|
| **stdio** | `command: /path/to/server` | Local subprocess |
| **Streamable HTTP** | `url: https://...` | Current MCP spec (POST) |
| **SSE** | `url: https://...` | Legacy SSE (GET), auto-fallback |

For `url`-based servers, multi-mcp tries Streamable HTTP first and falls back to legacy SSE automatically.

---

## Runtime API (SSE mode)

When running with `--transport sse`, a management API is available:

```bash
# List active servers
GET /mcp_servers

# Add a server at runtime
POST /mcp_servers
{"name": "new-server", "command": "/path/to/server"}

# Remove a server
DELETE /mcp_servers/{name}

# List all tools by server
GET /mcp_tools

# Health check
GET /health
```

Authenticate with `Authorization: Bearer <key>` (set `MULTI_MCP_API_KEY` env var to enable).

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│  Claude Code / Codex / Cursor / any MCP client   │
└─────────────────────┬────────────────────────────┘
                      │ stdio or SSE
              ┌───────▼────────┐
              │   multi-mcp    │
              │                │
              │ • YAML config  │
              │ • namespacing  │
              │ • tool filter  │
              │ • lazy loading │
              │ • audit log    │
              └──┬──────┬──────┘
                 │      │
    ┌────────────┘      └──────────────┐
    │                                  │
┌───▼──────────┐              ┌────────▼──────┐
│ always_on    │              │     lazy      │
│              │              │               │
│ github       │              │ exa (SSE)     │
│ obsidian     │              │ tavily        │
│              │              │ context7      │
│ (connected   │              │ seq-thinking  │
│  always)     │              │               │
└──────────────┘              │ (connects on  │
                              │  first call,  │
                              │  disconnects  │
                              │  after idle)  │
                              └───────────────┘
```

---

## Development

```bash
# Run tests
uv run python -m pytest

# Run specific test file
uv run python -m pytest tests/test_cache_manager.py -v

# Check what's configured
uv run python main.py status
uv run python main.py list
```

**Test coverage:** 30 tests across YAML config, merge logic, startup discovery, idle timeout, startup flow, CLI, and reconnect behavior.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MULTI_MCP_API_KEY` | Bearer token for SSE API auth |
| `MULTI_MCP_HOST` | SSE bind host (default: `127.0.0.1`) |
| `MULTI_MCP_PORT` | SSE bind port (default: `8085`) |
| `MULTI_MCP_LOG_LEVEL` | Log level: DEBUG, INFO, WARNING, ERROR |

---

## License

MIT
