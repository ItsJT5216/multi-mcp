from __future__ import annotations
from pathlib import Path
from typing import Literal, Optional
import yaml
from pydantic import BaseModel, Field, ValidationError
from src.utils.logger import get_logger

logger = get_logger("multi_mcp.config")


class ToolEntry(BaseModel):
    enabled: bool = True
    stale: bool = False
    description: str = ""
    input_schema: Optional[dict] = None


class ServerConfig(BaseModel):
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: Optional[str] = None
    type: Literal["stdio", "sse", "http", "streamablehttp"] = "stdio"
    always_on: bool = False
    idle_timeout_minutes: int = 5
    tool_call_timeout_seconds: Optional[float] = Field(default=None, gt=0)
    """Max seconds to wait for a tool call response from this server.

    When *None* (the default) tool calls wait indefinitely (previous behavior).
    When set, a hung backend returns a timeout error to the caller instead of
    stalling the request forever; repeated timeouts trip the proxy's
    circuit breaker and auto-quarantine the tool.
    """
    tools: dict[str, ToolEntry] = Field(default_factory=dict)
    triggers: list[str] = Field(default_factory=list)


class RetrievalSettings(BaseModel):
    """Configuration for the intelligent tool retrieval pipeline."""
    enabled: bool = False
    top_k: int = Field(default=15, ge=1)
    full_description_count: int = Field(default=3, ge=0)
    anchor_tools: list[str] = Field(default_factory=list)
    # Phase 2 fields
    shadow_mode: bool = False
    scorer: Literal["bmxf", "keyword"] = "bmxf"
    max_k: int = Field(default=20, ge=1, le=20)
    enable_routing_tool: bool = True
    enable_telemetry: bool = True
    telemetry_poll_interval: int = Field(default=30, ge=5)
    # Phase 4 rollout fields
    canary_percentage: float = Field(default=0.0, ge=0.0, le=100.0)
    rollout_stage: Literal["shadow", "canary", "ga"] = "shadow"
    # Logging
    log_path: str = ""


class ProfileConfig(BaseModel):
    """Per-profile tool allow-list. Keys are server names, values are lists of allowed tool names."""
    servers: dict[str, list[str]] = Field(default_factory=dict)


class MultiMCPConfig(BaseModel):
    servers: dict[str, ServerConfig] = Field(default_factory=dict)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)
    """Extra file/directory paths to scan for MCP configs (in addition to auto-detected editors)."""
    exclude_sources: list[str] = Field(default_factory=list)
    """File paths to skip during auto-scan (e.g. to ignore a specific editor's config)."""
    exclude_servers: list[str] = Field(default_factory=list)
    """Server names to never auto-import, even if found in editor configs."""
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    backup_dir: Optional[str] = None
    """Directory in which to store .bak files created before writing any tool config.

    When *None* (the default) each backup is placed in the same directory as the
    config file being modified.  Set this to an absolute path to collect all
    backups in a single location, e.g.::

        backup_dir: /home/alice/.config/multi-mcp/backups
    """


def load_config(path: Path) -> MultiMCPConfig:
    """Load YAML config from path. Returns empty config if file doesn't exist or is invalid."""
    if not path.exists():
        return MultiMCPConfig()
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        return MultiMCPConfig.model_validate(raw)
    except yaml.YAMLError as e:
        logger.error(f"❌ Invalid YAML in {path}: {e}")
        return MultiMCPConfig()
    except (ValidationError, TypeError, ValueError) as e:
        logger.error(f"❌ Invalid config schema at {path}: {e}")
        return MultiMCPConfig()
    except Exception as e:
        logger.error(f"❌ Unexpected error loading config from {path}: {e}")
        return MultiMCPConfig()


def save_config(config: MultiMCPConfig, path: Path) -> None:
    """Save config to YAML file, creating parent dirs as needed.

    Logs and re-raises on write error so callers can decide how to handle it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"❌ Failed to create config directory {path.parent}: {e}")
        raise
    try:
        with open(path, "w") as f:
            yaml.dump(
                config.model_dump(exclude_none=False),
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
    except OSError as e:
        logger.error(f"❌ Failed to write config to {path}: {e}")
        raise
