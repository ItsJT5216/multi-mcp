#!/bin/bash

# Multi-MCP Production Startup Script
# 
# This script starts the multi-mcp proxy server with:
# - SSE transport mode (for HTTP/network access)
# - Port 8085 (production default)
# - Production configuration (msc/mcp.json)
# - Lazy loading enabled
# - API key authentication (required for non-loopback binds)
#
# Required environment variables:
# - GITHUB_PERSONAL_ACCESS_TOKEN (for GitHub MCP server)
# - BRAVE_API_KEY (for Brave Search MCP server)
#
# Required environment variables for the default network bind:
# - MULTI_MCP_API_KEY (for MCP client authentication)
#
# Optional environment variables:
# - MULTI_MCP_MANAGEMENT_API_KEY (enables mutating management endpoints)
# - MULTI_MCP_HOST (default: 0.0.0.0)
# - MULTI_MCP_PORT (default: 8085)
# - MULTI_MCP_CONFIG (default: msc/mcp.json)

set -e

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check required environment variables
check_env_var() {
    local var_name=$1
    local var_value=${!var_name}
    
    if [ -z "$var_value" ]; then
        echo -e "${RED}❌ Error: Required environment variable $var_name is not set${NC}"
        return 1
    else
        echo -e "${GREEN}✅ $var_name is set${NC}"
        return 0
    fi
}

echo "🚀 Multi-MCP Production Startup"
echo "================================"
echo ""

# Check required API keys
echo "Checking required environment variables..."
all_set=true

if ! check_env_var "GITHUB_PERSONAL_ACCESS_TOKEN"; then
    all_set=false
fi

if ! check_env_var "BRAVE_API_KEY"; then
    all_set=false
fi

# Network deployments must fail closed without client authentication.
if [ -n "$MULTI_MCP_API_KEY" ]; then
    echo -e "${GREEN}✅ MULTI_MCP_API_KEY is set (authentication enabled)${NC}"
else
    echo -e "${RED}❌ MULTI_MCP_API_KEY is required for network deployments${NC}"
    all_set=false
fi

if [ -n "$MULTI_MCP_MANAGEMENT_API_KEY" ]; then
    echo -e "${GREEN}✅ MULTI_MCP_MANAGEMENT_API_KEY is set (management enabled)${NC}"
else
    echo -e "${YELLOW}⚠️  MULTI_MCP_MANAGEMENT_API_KEY is not set (management disabled)${NC}"
fi

echo ""

if [ "$all_set" = false ]; then
    echo -e "${RED}❌ Missing required environment variables. Please set them and try again.${NC}"
    echo ""
    echo "Example:"
    echo "  export GITHUB_PERSONAL_ACCESS_TOKEN='your-token-here'"
    echo "  export BRAVE_API_KEY='your-api-key-here'"
    echo "  export MULTI_MCP_API_KEY='your-client-secret'"
    echo "  export MULTI_MCP_MANAGEMENT_API_KEY='your-distinct-operator-secret' (optional)"
    echo ""
    exit 1
fi

# Set defaults
HOST="${MULTI_MCP_HOST:-0.0.0.0}"
PORT="${MULTI_MCP_PORT:-8083}"
CONFIG="${MULTI_MCP_CONFIG:-msc/mcp.json}"

echo "Configuration:"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Config: $CONFIG"
echo "  Auth: $([ -n "$MULTI_MCP_API_KEY" ] && echo 'Enabled' || echo 'Disabled')"
echo ""

# Verify config file exists
if [ ! -f "$CONFIG" ]; then
    echo -e "${RED}❌ Error: Config file not found: $CONFIG${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Config file found: $CONFIG${NC}"
echo ""

# Start the server
echo "Starting multi-mcp proxy server..."
echo "================================"
echo ""

exec python main.py start \
    --transport sse \
    --host "$HOST" \
    --port "$PORT" \
    --config "$CONFIG"
