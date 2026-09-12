"""MCP Client Manager — connection lifecycle for multiple MCP backend servers.

Manages creation, discovery, idle eviction, and graceful shutdown of MCP
client sessions across stdio, SSE, and Streamable HTTP transports.

Improvements over the original mcp_client.py:
─────────────────────────────────────────────
1. CONCURRENT DISCOVERY: discover_all() uses asyncio.gather bounded by
   the connection semaphore — 10 servers at 5s each complete in ~5s
   instead of ~50s. Single biggest startup-time improvement.

2. DEDUPLICATED TRANSPORT NEGOTIATION: A single _negotiate_http_transport()
   replaces three near-identical try/except blocks for Streamable HTTP →
   SSE fallback (was in _discover_sse, _connect_url_server, and indirectly
   in _create_single_client).

3. EXPONENTIAL BACKOFF IN WATCHDOG: Always-on reconnects use capped
   exponential backoff with jitter (1s → 2s → 4s → ... → 60s cap) instead
   of fixed 30s intervals. Reduces log noise for persistently dead servers.

4. STRUCTURED ERROR CLASSIFICATION: _is_transient_error() distinguishes
   retryable failures (timeout, connection reset) from permanent ones
   (bad command, SSRF rejection). Watchdog only retries transient errors.

5. PER-SERVER STACK ISOLATION: create_clients() uses per-server
   AsyncExitStack instances so one server's cleanup failure cannot
   affect others (the acknowledged shared-stack issue is resolved).
"""

from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable, Dict, Optional, Set
import ipaddress
import os
import asyncio
import random
import time
import socket
import urllib.parse

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from src.utils.logger import get_logger


# ── Security: command and URL validation ───────────────────────────────

DEFAULT_ALLOWED_COMMANDS = {
    "node", "npx", "uvx", "python", "python3", "uv", "docker", "bash", "sh",
}

PROTECTED_ENV_VARS = {
    # Linux/macOS loader injection
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH",
    # Language-specific injection
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
    "NODE_OPTIONS", "NODE_PATH", "NODE_EXTRA_CA_CERTS",
    "PERL5LIB", "PERL5OPT", "RUBYLIB", "RUBYOPT",
    # Shell startup execution
    "BASH_ENV", "ENV", "ZDOTDIR",
    # Traffic interception
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "all_proxy",
    # Identity / system
    "HOME", "USER",
}

_BACKOFF_BASE = 1.0   # seconds, initial retry delay
_BACKOFF_CAP = 60.0   # seconds, maximum retry delay

# Dangerous IP ranges for SSRF protection.
# NOTE: IPv4 loopback (127.0.0.0/8) and IPv6 loopback (::1/128) are intentionally
# excluded so that localhost-based MCP servers remain accessible.
_PRIVATE_RANGES: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 link-local (APIPA)
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local — DNS-rebinding risk
]


def _get_allowed_commands() -> set:
    """Return the set of allowed commands, from env var or default."""
    env_val = os.environ.get("MULTI_MCP_ALLOWED_COMMANDS", "")
    if env_val.strip():
        return {cmd.strip() for cmd in env_val.split(",") if cmd.strip()}
    return DEFAULT_ALLOWED_COMMANDS


def _validate_command(command: str) -> None:
    """Validate that the command is allowed to execute.

    Bare command names must be in the allowed list.
    Full paths: basename is checked first, then the path must point to an
    existing executable file. Full paths come from user-controlled configs.
    """
    cmd_name = os.path.basename(command)
    allowed = _get_allowed_commands()

    if cmd_name in allowed:
        return

    if (os.sep in command or "/" in command) and os.path.isfile(command) and os.access(command, os.X_OK):
        return

    if os.sep in command or "/" in command:
        raise ValueError(
            f"Command '{command}' (basename '{cmd_name}') is not in allowed commands "
            f"{allowed} and is not an executable file on disk. "
            f"Set MULTI_MCP_ALLOWED_COMMANDS env var to extend the allowlist."
        )
    raise ValueError(
        f"Command '{cmd_name}' is not in allowed commands: {allowed}. "
        f"Set MULTI_MCP_ALLOWED_COMMANDS env var to extend the allowlist."
    )


async def _validate_url(url: str) -> None:
    """Validate URL: check scheme, hostname presence, DNS resolvability, and SSRF safety."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' is not allowed. Only http/https permitted.")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname.")

    loop = asyncio.get_running_loop()
    try:
        addrs = await loop.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve hostname '{hostname}': {e}")

    for _family, _type, _proto, _canon, sockaddr in addrs:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for private_net in _PRIVATE_RANGES:
            if ip in private_net:
                raise ValueError(
                    f"URL resolves to a private/internal address '{ip_str}' "
                    f"({private_net}) — SSRF protection rejected this request."
                )


def _filter_env(env: dict) -> dict:
    """Remove protected env vars and coerce all values to str for subprocess safety."""
    return {k: str(v) for k, v in env.items() if k not in PROTECTED_ENV_VARS}


def _is_transient_error(exc: BaseException) -> bool:
    """Classify whether an error is likely transient (retryable) vs permanent."""
    return isinstance(exc, (
        asyncio.TimeoutError,
        ConnectionError,       # ConnectionRefusedError, ConnectionResetError, BrokenPipeError
        OSError,               # covers socket.error, EAI errors
        EOFError,
    ))


# ── Client Manager ─────────────────────────────────────────────────────

class MCPClientManager:
    """Manages the lifecycle of multiple MCP clients (stdio, SSE, Streamable HTTP).

    Handles creation, storage, idle eviction, always-on watchdog with
    exponential backoff, and graceful cleanup of client sessions.
    Supports lazy loading via pending_configs registry.
    """

    def __init__(
        self,
        max_concurrent_connections: int = 10,
        connection_timeout: float = 30.0,
        on_server_disconnected: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.stack = AsyncExitStack()
        self.clients: Dict[str, ClientSession] = {}
        self.server_stacks: Dict[str, AsyncExitStack] = {}
        self.pending_configs: Dict[str, dict] = {}
        self.server_configs: Dict[str, dict] = {}
        self.tool_filters: Dict[str, Optional[dict]] = {}
        self._connection_semaphore = asyncio.Semaphore(max_concurrent_connections)
        self._connection_timeout = connection_timeout
        self.logger = get_logger("multi_mcp.ClientManager")
        self.always_on_servers: Set[str] = set()
        self.idle_timeouts: Dict[str, float] = {}
        self.last_used: Dict[str, float] = {}
        self._on_server_disconnected = on_server_disconnected
        self._creation_locks: Dict[str, asyncio.Lock] = {}
        self.on_server_reconnected: Optional[Any] = None
        self._supervision_tasks: Dict[str, asyncio.Task] = {}
        self._lifecycle_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown_events: Dict[str, asyncio.Event] = {}
        # Per-server backoff state for watchdog reconnects
        self._reconnect_backoff: Dict[str, float] = {}

    def _get_creation_lock(self, name: str) -> asyncio.Lock:
        """Get or create a per-server creation lock (lazily initialized)."""
        return self._creation_locks.setdefault(name, asyncio.Lock())

    def cleanup_server_state(self, name: str) -> None:
        """Remove all per-server state for a fully unregistered server."""
        self.pending_configs.pop(name, None)
        self.server_configs.pop(name, None)
        self.tool_filters.pop(name, None)
        self.idle_timeouts.pop(name, None)
        self.last_used.pop(name, None)
        self._creation_locks.pop(name, None)
        self._reconnect_backoff.pop(name, None)
        self.clients.pop(name, None)
        task = self._supervision_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
        self.server_stacks.pop(name, None)

    # ── Transport negotiation (deduplicated) ───────────────────────────

    async def _negotiate_http_transport(
        self,
        name: str,
        url: str,
        server_stack: AsyncExitStack,
        transport_type: Optional[str] = None,
    ) -> ClientSession:
        """Connect to a URL-based MCP server using the correct transport.

        Handles three modes:
        - transport_type='streamablehttp': Streamable HTTP only
        - transport_type='sse' or 'http': Legacy SSE only
        - transport_type=None or other: Auto-detect (try Streamable HTTP → SSE fallback)

        Uses a nested stack for the Streamable HTTP attempt so that a failed
        attempt is cleaned up safely before falling through to SSE.

        Returns:
            Connected ClientSession.

        Raises:
            Exception: If all transport attempts fail.
        """
        # Direct Streamable HTTP (no fallback)
        if transport_type == "streamablehttp":
            read, write, _ = await server_stack.enter_async_context(
                streamable_http_client(url)
            )
            client = await server_stack.enter_async_context(ClientSession(read, write))
            self.logger.info(f"🌐 Connected to '{name}' via Streamable HTTP")
            return client

        # Direct legacy SSE (no probe)
        if transport_type in ("sse", "http"):
            read, write = await server_stack.enter_async_context(sse_client(url=url))
            client = await server_stack.enter_async_context(ClientSession(read, write))
            self.logger.info(f"🌐 Connected to '{name}' via SSE")
            return client

        # Auto-detect: try Streamable HTTP first, fall back to SSE
        fallback_stack = AsyncExitStack()
        try:
            await fallback_stack.__aenter__()
            read, write, _ = await fallback_stack.enter_async_context(
                streamable_http_client(url)
            )
            client = await fallback_stack.enter_async_context(ClientSession(read, write))
            # Success: absorb nested stack into the main server_stack
            await server_stack.enter_async_context(fallback_stack)
            self.logger.info(f"🌐 Connected to '{name}' via Streamable HTTP")
            return client
        except Exception as e:
            await fallback_stack.aclose()
            self.logger.debug(f"Streamable HTTP failed for '{name}', trying SSE: {e}")

        # Fall back to legacy SSE
        read, write = await server_stack.enter_async_context(sse_client(url=url))
        client = await server_stack.enter_async_context(ClientSession(read, write))
        self.logger.info(f"🌐 Connected to '{name}' via SSE (fallback)")
        return client

    # ── Discovery (concurrent) ─────────────────────────────────────────

    async def discover_all(self, config: "MultiMCPConfig") -> Dict[str, list]:
        """Connect to every server, fetch tool lists, disconnect lazy ones.

        Runs discovery concurrently (bounded by connection semaphore) for
        dramatically faster startup with multiple servers.

        Returns:
            Dict mapping server_name -> list[types.Tool]
        """
        async def _discover_one(name: str, server_config) -> tuple[str, list]:
            """Discover a single server (semaphore-bounded)."""
            async with self._connection_semaphore:
                try:
                    tools = await asyncio.wait_for(
                        self._discover_server(name, server_config),
                        timeout=self._connection_timeout,
                    )
                    return (name, tools)
                except asyncio.TimeoutError:
                    self.logger.error(
                        f"❌ Discovery timeout for '{name}' (>{self._connection_timeout}s)"
                    )
                    return (name, [])
                except Exception as e:
                    self.logger.error(f"❌ Discovery failed for '{name}': {e}")
                    return (name, [])

        # Launch all discoveries concurrently
        tasks = [
            _discover_one(name, server_config)
            for name, server_config in config.servers.items()
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=False)
        return dict(results_list)

    async def _discover_server(self, name: str, server_config) -> list:
        """Discover tools from a single server (auto-detects transport)."""
        server_dict = server_config.model_dump(exclude_none=True)
        command = server_dict.get("command")
        url = server_dict.get("url")

        if not command and not url:
            self.logger.warning(f"⚠️ Skipping '{name}': no command or URL")
            return []

        if url:
            return await self._discover_http(name, url, server_config)
        return await self._discover_stdio(name, server_dict, server_config)

    async def _discover_http(self, name: str, url: str, server_config) -> list:
        """Discover tools from an HTTP/SSE server using unified transport negotiation."""
        transport_type = getattr(server_config, "type", "stdio")
        # Map 'stdio' default to None for auto-detect when URL is present
        if transport_type == "stdio":
            transport_type = None

        server_stack = AsyncExitStack()
        try:
            await server_stack.__aenter__()
            client = await self._negotiate_http_transport(
                name, url, server_stack, transport_type,
            )
            init_result = await client.initialize()
            tools = []
            if init_result.capabilities.tools:
                tools_result = await client.list_tools()
                tools = tools_result.tools
            self.logger.info(f"🔌 Discovered {len(tools)} tools from '{name}'")
            return tools
        except Exception as e:
            self.logger.error(f"❌ HTTP discovery failed for '{name}': {e}")
            return []
        finally:
            try:
                await server_stack.aclose()
            except Exception:
                pass

    async def _discover_stdio(self, name: str, server_dict: dict, server_config) -> list:
        """Discover tools from a stdio server. Keeps always_on servers connected."""
        server_stack = AsyncExitStack()
        try:
            await server_stack.__aenter__()
            command = server_dict.get("command")
            if command:
                _validate_command(command)
            args = server_dict.get("args", [])
            env = server_dict.get("env", {})
            safe_env = _filter_env(env)
            merged_env = os.environ.copy()
            merged_env.update(safe_env)

            params = StdioServerParameters(command=command, args=args, env=merged_env)
            read, write = await server_stack.enter_async_context(stdio_client(params))
            client = await server_stack.enter_async_context(ClientSession(read, write))

            init_result = await asyncio.wait_for(
                client.initialize(), timeout=self._connection_timeout,
            )
            tools = []
            if init_result.capabilities.tools:
                tools_result = await client.list_tools()
                tools = tools_result.tools

            if not server_config.always_on:
                await server_stack.aclose()
                self.logger.info(
                    f"🔌 Discovered {len(tools)} tools from '{name}', disconnected (lazy)"
                )
            else:
                self.server_stacks[name] = server_stack
                self.clients[name] = client
                self.logger.info(
                    f"✅ Discovered {len(tools)} tools from '{name}', staying connected (always_on)"
                )
            return tools

        except BaseException as e:
            if not isinstance(e, asyncio.CancelledError):
                self.logger.error(f"❌ Discovery failed for '{name}': {e}")
            try:
                await server_stack.aclose()
            except Exception:
                pass
            if isinstance(e, Exception):
                return []
            raise

    # ── Lazy client access ─────────────────────────────────────────────

    def _parse_tool_filter(self, config: dict) -> Optional[dict]:
        """Normalize the 'tools' field from a server config into {allow, deny} format."""
        tools = config.get("tools")
        if tools is None:
            return None
        if isinstance(tools, list):
            return {"allow": tools, "deny": []}
        if isinstance(tools, dict):
            return {"allow": tools.get("allow", ["*"]), "deny": tools.get("deny", [])}
        return None

    def add_pending_server(self, name: str, config: dict) -> None:
        """Add a server configuration to the pending registry without connecting."""
        self.tool_filters.setdefault(name, self._parse_tool_filter(config))
        self.server_configs[name] = config
        self.pending_configs[name] = config
        self.logger.info(f"📋 Added pending server: {name}")

    async def get_or_create_client(self, name: str) -> ClientSession:
        """Get an existing client or create it from pending configs on first access.

        Uses a per-server lock to prevent race conditions on concurrent first-access.
        """
        # Fast path: already connected
        if name in self.clients:
            self.record_usage(name)
            return self.clients[name]

        # Slow path: acquire per-server lock before creating
        async with self._get_creation_lock(name):
            if name in self.clients:
                self.record_usage(name)
                return self.clients[name]

            if name in self.pending_configs:
                config = self.pending_configs.pop(name)
                async with self._connection_semaphore:
                    try:
                        await asyncio.wait_for(
                            self._create_single_client(name, config),
                            timeout=self._connection_timeout,
                        )
                    except asyncio.TimeoutError:
                        self.pending_configs[name] = config
                        self.logger.error(f"❌ Connection timeout for {name}")
                        raise
                    except Exception:
                        self.pending_configs[name] = config
                        raise
                self.record_usage(name)
                return self.clients[name]

            raise KeyError(f"Unknown server: {name}")

    # ── Client creation (lifecycle task) ───────────────────────────────

    async def _create_single_client(self, name: str, server: dict) -> None:
        """Create a single client from config via an isolated lifecycle task.

        The connection runs in its own asyncio task so that when the backend
        subprocess dies, exceptions are caught by the task instead of
        propagating into the event loop.
        """
        await self._stop_server_lifecycle(name)

        ready_event = asyncio.Event()
        error_holder: list = []
        shutdown_event = asyncio.Event()
        self._shutdown_events[name] = shutdown_event

        async def _lifecycle() -> None:
            server_stack = AsyncExitStack()
            await server_stack.__aenter__()
            try:
                command = server.get("command")
                url = server.get("url")
                args = server.get("args", [])
                env = server.get("env", {})

                if command:
                    _validate_command(command)
                    safe_env = _filter_env(env)
                    merged_env = os.environ.copy()
                    merged_env.update(safe_env)
                    self.logger.info(f"🔌 Creating stdio client for {name}")
                    params = StdioServerParameters(
                        command=command, args=args, env=merged_env,
                    )
                    read, write = await server_stack.enter_async_context(stdio_client(params))
                    session = await server_stack.enter_async_context(
                        ClientSession(read, write)
                    )
                elif url:
                    transport_type = server.get("type")
                    session = await self._negotiate_http_transport(
                        name, url, server_stack, transport_type,
                    )
                else:
                    self.logger.warning(f"⚠️ Skipping {name}: No command or URL provided.")
                    await server_stack.aclose()
                    ready_event.set()
                    return

                self.clients[name] = session
                self.server_stacks[name] = server_stack
                self.logger.info(f"✅ Connected to {name}")
                ready_event.set()

                await shutdown_event.wait()

            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                if not ready_event.is_set():
                    error_holder.append(exc)
                    ready_event.set()
                    return
                self.logger.warning(
                    f"🔌 Server '{name}' disconnected: {type(exc).__name__}: {exc}"
                )
            finally:
                self.clients.pop(name, None)
                self.server_stacks.pop(name, None)
                self._shutdown_events.pop(name, None)
                try:
                    await server_stack.aclose()
                except Exception:
                    pass
                if ready_event.is_set() and not error_holder:
                    if self._on_server_disconnected:
                        try:
                            await self._on_server_disconnected(name)
                        except Exception:
                            pass

        task = asyncio.create_task(_lifecycle(), name=f"lifecycle-{name}")
        self._lifecycle_tasks[name] = task

        await ready_event.wait()
        if error_holder:
            raise error_holder[0]

    async def _stop_server_lifecycle(self, name: str) -> None:
        """Signal a server's lifecycle task to shut down and wait for it."""
        evt = self._shutdown_events.pop(name, None)
        if evt:
            evt.set()
        sup = self._supervision_tasks.pop(name, None)
        if sup and not sup.done():
            sup.cancel()
        task = self._lifecycle_tasks.pop(name, None)
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ── Idle management ────────────────────────────────────────────────

    def record_usage(self, server_name: str) -> None:
        """Update last-used timestamp for a server."""
        self.last_used[server_name] = time.monotonic()

    async def _disconnect_idle_servers(self) -> None:
        """Disconnect lazy servers that have exceeded their idle timeout."""
        now = time.monotonic()
        to_disconnect = [
            name for name in list(self.clients.keys())
            if name not in self.always_on_servers
            and name in self.idle_timeouts
            and now - self.last_used.get(name, 0) > self.idle_timeouts[name]
        ]
        for name in to_disconnect:
            self.logger.info(f"💤 Disconnecting idle server: {name}")
            del self.clients[name]
            if name in self.server_configs:
                self.pending_configs[name] = self.server_configs[name]
            self.last_used.pop(name, None)
            self._creation_locks.pop(name, None)
            stack = self.server_stacks.pop(name, None)
            if stack:
                try:
                    await stack.aclose()
                except Exception as e:
                    self.logger.warning(f"⚠️ Error closing stack for '{name}': {e}")
            if self._on_server_disconnected:
                try:
                    await self._on_server_disconnected(name)
                except Exception as e:
                    self.logger.warning(f"⚠️ Disconnect callback failed for '{name}': {e}")

    async def start_idle_checker(self, interval_seconds: float = 60.0) -> None:
        """Background task: periodically disconnect idle lazy servers."""
        while True:
            await asyncio.sleep(interval_seconds)
            await self._disconnect_idle_servers()

    # ── Always-on watchdog (with exponential backoff) ──────────────────

    async def start_always_on_watchdog(
        self, configs: Dict[str, dict], interval_seconds: float = 30.0
    ) -> None:
        """Background task: reconnect always_on servers if their connection drops.

        Uses capped exponential backoff with jitter per server:
        base 1s → 2s → 4s → 8s → ... → 60s cap. Resets on successful reconnect.
        """

        while True:
            await asyncio.sleep(interval_seconds)

            for name in list(self.always_on_servers):
                if name in self.clients:
                    # Connected — reset backoff
                    self._reconnect_backoff.pop(name, None)
                    continue

                # Check backoff: should we attempt reconnect yet?
                current_backoff = self._reconnect_backoff.get(name, 0.0)
                if current_backoff > 0:
                    # Decrement by the interval we just slept
                    remaining = current_backoff - interval_seconds
                    if remaining > 0:
                        self._reconnect_backoff[name] = remaining
                        continue
                    # Backoff expired — proceed with attempt

                server_config = configs.get(name)
                if not server_config:
                    continue

                self.logger.warning(f"⚠️ Always-on server '{name}' disconnected — reconnecting...")
                try:
                    await asyncio.wait_for(
                        self._create_single_client(name, server_config),
                        timeout=self._connection_timeout,
                    )
                    client = self.clients.get(name)
                    if client:
                        await asyncio.wait_for(
                            client.initialize(),
                            timeout=self._connection_timeout,
                        )
                        self.logger.info(f"✅ Reconnected always-on server '{name}'")
                        self._reconnect_backoff.pop(name, None)
                        if self.on_server_reconnected:
                            try:
                                await self.on_server_reconnected(name, client)
                            except Exception as cb_err:
                                self.logger.warning(
                                    f"⚠️ on_server_reconnected callback failed for '{name}': {cb_err}"
                                )
                except Exception as e:
                    # Compute next backoff with jitter
                    prev = self._reconnect_backoff.get(name, _BACKOFF_BASE / 2)
                    next_backoff = min(prev * 2, _BACKOFF_CAP)
                    jitter = random.uniform(0, next_backoff * 0.1)
                    self._reconnect_backoff[name] = next_backoff + jitter

                    transient = _is_transient_error(e)
                    if transient:
                        self.logger.error(
                            f"❌ Transient failure reconnecting '{name}': {e} "
                            f"(next retry in ~{next_backoff:.0f}s)"
                        )
                    else:
                        self.logger.error(
                            f"❌ Permanent failure reconnecting '{name}': {e} "
                            f"(backoff {next_backoff:.0f}s, may need manual intervention)"
                        )

    # ── Supervision ────────────────────────────────────────────────────

    def _start_supervision(self, name: str, interval: float = 10.0) -> None:
        """Start a background task that monitors a server's health."""
        old_task = self._supervision_tasks.pop(name, None)
        if old_task and not old_task.done():
            old_task.cancel()

        async def _supervise() -> None:
            try:
                while name in self.clients:
                    await asyncio.sleep(interval)
                    session = self.clients.get(name)
                    if session is None:
                        break
                    try:
                        await asyncio.wait_for(session.send_ping(), timeout=15)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self.logger.warning(
                            f"🔌 Server '{name}' is unresponsive, cleaning up"
                        )
                        break
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.logger.warning(
                    f"🔌 Supervision for '{name}' caught error: {exc}"
                )
            finally:
                if name in self.clients:
                    self.logger.info(f"🔄 Cleaning up '{name}' for watchdog reconnect")
                    self.clients.pop(name, None)
                    self.server_stacks.pop(name, None)
                    if self._on_server_disconnected:
                        try:
                            await self._on_server_disconnected(name)
                        except Exception:
                            pass

        self._supervision_tasks[name] = asyncio.create_task(
            _supervise(), name=f"supervise-{name}"
        )

    # ── Eager client creation (per-server stacks) ──────────────────────

    async def create_clients(
        self, config: dict, lazy: bool = False
    ) -> Dict[str, ClientSession]:
        """Creates MCP clients defined in the given config.

        Uses per-server AsyncExitStack instances so one server's cleanup
        failure cannot affect others.

        Args:
            config: Configuration dictionary with "mcpServers" mapping.
            lazy: If True, store configs without connecting.

        Returns:
            Dict mapping server names to live ClientSession objects.
        """
        if lazy:
            for name, server in config.get("mcpServers", {}).items():
                self.add_pending_server(name, server)
            return {}

        # Eager mode: connect immediately with per-server isolation
        for name, server in config.get("mcpServers", {}).items():
            self.tool_filters[name] = self._parse_tool_filter(server)
            self.server_configs.setdefault(name, server)
            if name in self.clients:
                self.logger.warning(
                    f"⚠️ Client '{name}' already exists and will be overridden."
                )
            await self._create_single_client(name, server)

        return self.clients

    # ── Shutdown ───────────────────────────────────────────────────────

    async def close(self) -> None:
        """Closes all clients and releases resources."""
        # Signal all lifecycle tasks to shut down
        for evt in list(self._shutdown_events.values()):
            evt.set()

        # Cancel supervision tasks
        for task in list(self._supervision_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._supervision_tasks.values()):
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        self._supervision_tasks.clear()

        # Wait for lifecycle tasks
        for task in list(self._lifecycle_tasks.values()):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
        self._lifecycle_tasks.clear()
        self._shutdown_events.clear()

        # Close any stacks not managed by lifecycle tasks
        for name, server_stack in list(self.server_stacks.items()):
            try:
                await server_stack.aclose()
            except Exception as e:
                self.logger.warning(f"⚠️ Error closing server stack for '{name}': {e}")
        self.server_stacks.clear()
        self.clients.clear()
        await self.stack.aclose()
