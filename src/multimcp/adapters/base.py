"""Base class and platform utilities for MCP config adapters."""
from __future__ import annotations

import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional


def _current_platform() -> str:
    """Return a normalised platform string: 'macos', 'windows', or 'linux'."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


class MCPConfigAdapter(ABC):
    """Abstract base class for per-tool MCP configuration adapters.

    Each concrete subclass knows how to read and write the MCP server
    configuration for a specific AI tool (Claude Desktop, Zed, etc.).
    """

    tool_name: str
    display_name: str
    config_format: str  # "json" | "yaml" | "toml" | "json5"
    supported_platforms: List[str]

    #: Optional directory for backup files.  When *None* (the default) each
    #: backup is written beside the source config file.  Set by the caller
    #: (e.g. the adapter registry) from the YAML ``backup_dir`` setting.
    backup_dir: Optional[Path] = None

    def is_supported(self) -> bool:
        """Return True when this adapter is usable on the current platform."""
        return _current_platform() in self.supported_platforms

    def _backup(self, path: Path) -> None:
        """Create a ``.bak`` copy of *path* before it is overwritten.

        The backup is placed in ``self.backup_dir`` when set (with a
        ``{tool_name}_`` prefix to avoid collisions between tools that share a
        config filename), otherwise in the same directory as *path*.  The
        method is a no-op when *path* does not exist (nothing to back up).
        """
        if not path.exists():
            return
        dest_dir = self.backup_dir if self.backup_dir is not None else path.parent
        dest_name = f"{self.tool_name}_{path.name}.bak" if self.backup_dir else path.name + ".bak"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest_dir / dest_name)

    @abstractmethod
    def config_path(self) -> Optional[Path]:
        """Return the path to the tool's MCP config file (or directory)."""

    @abstractmethod
    def read_config(self) -> Dict:
        """Read and return the current config as a plain dict."""

    @abstractmethod
    def write_config(self, data: Dict) -> None:
        """Persist *data* as the tool's config."""

    @abstractmethod
    def register_server(self, name: str, config: Dict) -> None:
        """Add or update an MCP server entry in the tool's config."""

    @abstractmethod
    def discover_servers(self) -> Dict[str, Dict]:
        """Return a mapping of server-name → server-config for this tool."""
