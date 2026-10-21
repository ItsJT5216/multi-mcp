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
        """Replace comments and trailing commas with whitespace, preserving strings."""
        result = list(content)
        commas = []
        index = 0
        length = len(content)
        while index < length:
            character = content[index]
            if character == '"':
                index += 1
                while index < length:
                    if content[index] == '\\':
                        index += 2
                    elif content[index] == '"':
                        index += 1
                        break
                    else:
                        index += 1
                continue
            if content.startswith('//', index):
                end = index + 2
                while end < length and content[end] not in '\r\n':
                    end += 1
                result[index:end] = ' ' * (end - index)
                index = end
                continue
            if content.startswith('/*', index):
                end = content.find('*/', index + 2)
                if end == -1:
                    raise json.JSONDecodeError('Unterminated block comment', content, index)
                end += 2
                for position in range(index, end):
                    if result[position] not in '\r\n':
                        result[position] = ' '
                index = end
                continue
            if content.startswith(('NaN', 'Infinity', '-Infinity'), index):
                raise json.JSONDecodeError('Invalid JSON constant', content, index)
            if character == ',':
                commas.append(index)
            index += 1
        for index in commas:
            following = index + 1
            while following < length and result[following] in ' \t\r\n':
                following += 1
            preceding = index - 1
            while preceding >= 0 and result[preceding] in ' \t\r\n':
                preceding -= 1
            if (following < length and result[following] in '}]'
                    and preceding >= 0 and result[preceding] not in ',[{:'):
                result[index] = ' '
        return ''.join(result)

    def read_config(self) -> Dict:
        """Read OpenCode's config, returning {} if absent.

        Supports JSONC comments and trailing commas without changing string
        literals. Invalid content raises JSONDecodeError before any write.
        """
        path = self.config_path()
        if path is None or not path.exists():
            return {}
        content = path.read_text(encoding="utf-8")
        content = self._strip_jsonc_comments(content)
        return json.loads(content)

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