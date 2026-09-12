"""OpenCode MCP config adapter."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from src.multimcp.adapters.base import MCPConfigAdapter


class OpenCodeAdapter(MCPConfigAdapter):
    """Adapter for the OpenCode AI assistant.

    OpenCode stores its full configuration (including MCP servers under the
    ``mcp`` key) in a platform-specific JSON or JSONC file:

    * **Linux / macOS**: ``~/.config/opencode/opencode.json``
    * **Windows**: ``%APPDATA%\\opencode\\opencode.jsonc``

    A project-level ``opencode.json`` in the current working directory is
    checked first; the user-level path is used as the fallback destination for
    writes when no project file exists.
    """

    tool_name = "opencode"
    display_name = "OpenCode"
    config_format = "json"
    supported_platforms = ["macos", "linux", "windows"]

    def _user_config_path(self) -> Path:
        """Return the user-level OpenCode config path."""
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            return Path(appdata) / "opencode" / "opencode.jsonc" if appdata else Path.home() / "AppData" / "Roaming" / "opencode" / "opencode.jsonc"
        return Path.home() / ".config" / "opencode" / "opencode.json"

    def config_path(self) -> Optional[Path]:
        """Return the active OpenCode config path.

        Checks for a project-local ``opencode.json`` first; falls back to the
        user-level config path.
        """
        project = Path.cwd() / "opencode.json"
        if project.exists():
            return project
        return self._user_config_path()

    def _strip_jsonc_comments(self, content: str) -> str:
        """Strip comments and trailing commas from JSONC content.

        The scan is string-aware: ``//`` and ``/* */`` sequences inside JSON
        string literals (e.g. ``"http://example.com"``) are preserved.
        """
        result: list[str] = []
        i = 0
        n = len(content)
        in_string = False
        while i < n:
            ch = content[i]
            if in_string:
                result.append(ch)
                if ch == "\\" and i + 1 < n:
                    result.append(content[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
                i += 1
                continue
            if ch == '"':
                in_string = True
                result.append(ch)
                i += 1
                continue
            if ch == "/" and i + 1 < n:
                nxt = content[i + 1]
                if nxt == "/":
                    # Single-line comment: skip to end of line (keep the newline).
                    while i < n and content[i] != "\n":
                        i += 1
                    continue
                if nxt == "*":
                    # Multi-line comment: skip past the closing */.
                    i += 2
                    while i + 1 < n and not (content[i] == "*" and content[i + 1] == "/"):
                        i += 1
                    i += 2
                    continue
            result.append(ch)
            i += 1
        stripped = "".join(result)
        # Remove trailing commas before closing braces/brackets, again string-aware.
        result = []
        in_string = False
        i = 0
        n = len(stripped)
        while i < n:
            ch = stripped[i]
            if in_string:
                result.append(ch)
                if ch == "\\" and i + 1 < n:
                    result.append(stripped[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
                i += 1
                continue
            if ch == '"':
                in_string = True
                result.append(ch)
                i += 1
                continue
            if ch == ",":
                j = i + 1
                while j < n and stripped[j].isspace():
                    j += 1
                if j < n and stripped[j] in "}]":
                    i += 1  # drop the trailing comma
                    continue
            result.append(ch)
            i += 1
        return "".join(result)

    def read_config(self) -> Dict:
        """Read OpenCode's config, returning {} if absent.

        Supports both JSON and JSONC formats. Plain JSON is parsed as-is;
        comment/trailing-comma stripping is applied only when the initial
        parse fails, so ``//`` sequences inside string values (e.g. URLs)
        never corrupt valid JSON.
        """
        path = self.config_path()
        if path is None or not path.exists():
            return {}
        content = path.read_text(encoding="utf-8")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return json.loads(self._strip_jsonc_comments(content))

    def write_config(self, data: Dict) -> None:
        """Write *data* to OpenCode's config file."""
        path = self.config_path()
        assert path is not None
        self._backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def register_server(self, name: str, config: Dict) -> None:
        """Add or update an MCP server entry under the ``mcp`` key."""
        data = self.read_config()
        data.setdefault("mcp", {})[name] = config
        self.write_config(data)

    def discover_servers(self) -> Dict[str, Dict]:
        """Return all servers from OpenCode's ``mcp`` key."""
        return self.read_config().get("mcp", {})