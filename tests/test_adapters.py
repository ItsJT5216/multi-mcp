"""Tests for the static per-tool MCP config adapters.

Covers: config_path resolution, read_config, register_server, discover_servers
for claude_desktop, zed, continue_dev, cline, and gemini_cli.  Additional
adapters follow the same patterns and are exercised through the registry tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from src.multimcp.adapters import AdapterRegistry, get_adapter, list_adapters
from src.multimcp.adapters.base import MCPConfigAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_json_config(tmp_path: Path, filename: str, data: dict) -> Path:
    """Write *data* as JSON to *tmp_path/filename* and return the path."""
    p = tmp_path / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestAdapterRegistry:
    def test_all_returns_16_adapters(self) -> None:
        from src.multimcp.adapters.registry import _ALL_ADAPTER_CLASSES
        registry = AdapterRegistry()
        assert len(registry.all()) == len(_ALL_ADAPTER_CLASSES)

    def test_get_returns_correct_adapter(self) -> None:
        registry = AdapterRegistry()
        adapter = registry.get("claude_desktop")
        assert adapter is not None
        assert adapter.tool_name == "claude_desktop"
        assert adapter.display_name == "Claude Desktop"

    def test_get_unknown_returns_none(self) -> None:
        registry = AdapterRegistry()
        assert registry.get("nonexistent_tool") is None

    def test_all_adapters_sorted(self) -> None:
        adapters = list_adapters()
        names = [a.tool_name for a in adapters]
        assert names == sorted(names)

    def test_get_adapter_module_function(self) -> None:
        adapter = get_adapter("zed")
        assert adapter is not None
        assert adapter.tool_name == "zed"

    def test_every_adapter_has_required_attributes(self) -> None:
        for adapter in list_adapters():
            assert isinstance(adapter.tool_name, str) and adapter.tool_name
            assert isinstance(adapter.display_name, str) and adapter.display_name
            assert adapter.config_format in {"json", "yaml", "toml", "json5"}
            assert isinstance(adapter.supported_platforms, list)
            assert len(adapter.supported_platforms) >= 1

    def test_is_supported_returns_bool(self) -> None:
        for adapter in list_adapters():
            result = adapter.is_supported()
            assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Claude Desktop
# ---------------------------------------------------------------------------

class TestClaudeDesktopAdapter:
    def _adapter(self):
        return get_adapter("claude_desktop")

    def test_config_path_macos(self) -> None:
        with patch("sys.platform", "darwin"):
            import importlib
            from src.multimcp.adapters.tools import claude_desktop

            importlib.reload(claude_desktop)
            adapter = claude_desktop.ClaudeDesktopAdapter()
            path = adapter.config_path()
        assert path is not None
        assert "Application Support" in str(path)
        assert "claude_desktop_config.json" in str(path)

    def test_config_path_linux(self) -> None:
        with patch("sys.platform", "linux"):
            from src.multimcp.adapters.tools.claude_desktop import ClaudeDesktopAdapter
            adapter = ClaudeDesktopAdapter()
            path = adapter.config_path()
        assert path is not None
        assert ".config/Claude" in str(path)

    def test_config_path_windows(self) -> None:
        with patch("sys.platform", "win32"), patch.dict("os.environ", {"APPDATA": "C:\\Users\\test\\AppData\\Roaming"}):
            from src.multimcp.adapters.tools.claude_desktop import ClaudeDesktopAdapter
            adapter = ClaudeDesktopAdapter()
            path = adapter.config_path()
        assert path is not None
        assert "Claude" in str(path)

    def test_read_config_missing_file(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "missing.json"):
            result = adapter.read_config()
        assert result == {}

    def test_read_config_parses_json(self, tmp_path: Path) -> None:
        data = {"mcpServers": {"my-server": {"command": "python", "args": ["s.py"]}}}
        p = _make_json_config(tmp_path, "claude_desktop_config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            result = adapter.read_config()
        assert result == data

    def test_register_server_creates_file(self, tmp_path: Path) -> None:
        p = tmp_path / "claude_desktop_config.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("test-server", {"command": "uvx", "args": ["mcp-server"]})
            config = json.loads(p.read_text())
        assert "test-server" in config["mcpServers"]
        assert config["mcpServers"]["test-server"]["command"] == "uvx"

    def test_register_server_merges_without_overwriting(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"existing": {"command": "python"}}}
        p = _make_json_config(tmp_path, "claude_desktop_config.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new-server", {"command": "node"})
            config = json.loads(p.read_text())
        assert "existing" in config["mcpServers"]
        assert "new-server" in config["mcpServers"]

    def test_discover_servers_returns_correct_structure(self, tmp_path: Path) -> None:
        data = {
            "mcpServers": {
                "weather": {"command": "python", "args": ["weather.py"]},
                "github": {"command": "npx", "args": ["-y", "@mcp/github"]},
            }
        }
        p = _make_json_config(tmp_path, "claude_desktop_config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert set(servers.keys()) == {"weather", "github"}
        assert servers["weather"]["command"] == "python"

    def test_discover_servers_empty_config(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert servers == {}


# ---------------------------------------------------------------------------
# Zed
# ---------------------------------------------------------------------------

class TestZedAdapter:
    def _adapter(self):
        return get_adapter("zed")

    def test_config_path_returns_settings_json(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert path.name == "settings.json"

    def test_read_config_missing_file(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "settings.json"):
            assert adapter.read_config() == {}

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        adapter = self._adapter()
        server_cfg = {"command": {"path": "python", "args": ["s.py"], "env": {}}}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("my-zed-server", server_cfg)
            servers = adapter.discover_servers()
        assert "my-zed-server" in servers
        assert servers["my-zed-server"] == server_cfg

    def test_register_preserves_existing_settings(self, tmp_path: Path) -> None:
        existing = {
            "theme": "One Dark",
            "context_servers": {"old-server": {"command": {"path": "node"}}},
        }
        p = _make_json_config(tmp_path, "settings.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new-server", {"command": {"path": "python"}})
            config = json.loads(p.read_text())
        assert config["theme"] == "One Dark"
        assert "old-server" in config["context_servers"]
        assert "new-server" in config["context_servers"]

    def test_discover_servers_uses_context_servers_key(self, tmp_path: Path) -> None:
        data = {"context_servers": {"srv1": {"command": {"path": "node"}}}}
        p = _make_json_config(tmp_path, "settings.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert "srv1" in servers


# ---------------------------------------------------------------------------
# Continue.dev
# ---------------------------------------------------------------------------

class TestContinueDevAdapter:
    def _adapter(self):
        return get_adapter("continue_dev")

    def test_config_path_returns_config_yaml(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert path.name == "config.yaml"
        assert ".continue" in str(path)

    def test_read_config_empty_when_file_missing(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "config.yaml"):
            assert adapter.read_config() == {}

    def test_register_server_writes_yaml_file(self, tmp_path: Path) -> None:
        p = tmp_path / "config.yaml"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("weather", {"command": "python", "args": ["w.py"]})
        data = yaml.safe_load(p.read_text())
        assert any(s["name"] == "weather" for s in data["mcpServers"])

    def test_discover_servers_reads_yaml(self, tmp_path: Path) -> None:
        content = yaml.dump({
            "mcpServers": [
                {"name": "weather", "command": "python", "args": ["w.py"]},
                {"name": "github", "url": "http://localhost:9090/sse"},
            ]
        })
        p = tmp_path / "config.yaml"
        p.write_text(content, encoding="utf-8")
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert "weather" in servers
        assert "github" in servers

    def test_register_server_overwrites_existing_entry(self, tmp_path: Path) -> None:
        content = yaml.dump({"mcpServers": [{"name": "weather", "command": "old"}]})
        p = tmp_path / "config.yaml"
        p.write_text(content, encoding="utf-8")
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("weather", {"command": "new"})
            servers = adapter.discover_servers()
        assert servers["weather"]["command"] == "new"
        # Should not have duplicates
        raw = yaml.safe_load(p.read_text())
        assert len([s for s in raw["mcpServers"] if s["name"] == "weather"]) == 1


# ---------------------------------------------------------------------------
# Cline
# ---------------------------------------------------------------------------

class TestClineAdapter:
    def _adapter(self):
        return get_adapter("cline")

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert "cline_mcp_settings.json" in str(path)

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "missing.json"):
            assert adapter.read_config() == {}

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "cline_mcp_settings.json"
        adapter = self._adapter()
        cfg = {"command": "node", "args": ["server.js"], "disabled": False, "autoApprove": []}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("my-cline-server", cfg)
            servers = adapter.discover_servers()
        assert "my-cline-server" in servers
        assert servers["my-cline-server"]["command"] == "node"

    def test_register_does_not_corrupt_existing(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"old": {"command": "python"}}}
        p = _make_json_config(tmp_path, "cline_mcp_settings.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new", {"command": "node"})
            servers = adapter.discover_servers()
        assert "old" in servers
        assert "new" in servers


# ---------------------------------------------------------------------------
# Gemini CLI
# ---------------------------------------------------------------------------

class TestGeminiCLIAdapter:
    def _adapter(self):
        return get_adapter("gemini_cli")

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert ".gemini" in str(path)
        assert path.name == "settings.json"

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "settings.json"):
            assert adapter.read_config() == {}

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        adapter = self._adapter()
        cfg = {"command": "python", "args": ["mcp_server.py"], "timeout": 30000, "trust": False}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("multi-mcp", cfg)
            servers = adapter.discover_servers()
        assert "multi-mcp" in servers
        assert servers["multi-mcp"]["command"] == "python"

    def test_register_preserves_other_servers(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"brave": {"command": "npx"}}}
        p = _make_json_config(tmp_path, "settings.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("multi-mcp", {"command": "python"})
            servers = adapter.discover_servers()
        assert "brave" in servers
        assert "multi-mcp" in servers


# ---------------------------------------------------------------------------
# OpenClaw (JSON5 comment stripping)
# ---------------------------------------------------------------------------

class TestOpenClawAdapter:
    def _adapter(self):
        return get_adapter("openclaw")

    def test_strip_line_comments(self) -> None:
        from src.multimcp.adapters.tools.openclaw import _strip_json5_comments
        result = _strip_json5_comments('{"key": "value"} // comment')
        assert "//" not in result
        assert '"key"' in result

    def test_strip_block_comments(self) -> None:
        from src.multimcp.adapters.tools.openclaw import _strip_json5_comments
        result = _strip_json5_comments('{"key": /* block */ "value"}')
        assert "/*" not in result
        assert '"key"' in result

    def test_url_in_string_not_stripped(self) -> None:
        from src.multimcp.adapters.tools.openclaw import _strip_json5_comments
        text = '{"url": "https://example.com/path"}'
        result = _strip_json5_comments(text)
        assert "https://example.com/path" in result

    def test_block_comment_in_string_not_stripped(self) -> None:
        from src.multimcp.adapters.tools.openclaw import _strip_json5_comments
        text = '{"desc": "Use /* for comments */"}'
        result = _strip_json5_comments(text)
        assert "/* for comments */" in result

    def test_read_json5_with_comments(self, tmp_path: Path) -> None:
        content = (
            '{\n'
            '  // This is a comment\n'
            '  "mcpServers": {\n'
            '    "srv1": { "command": "python" } /* inline */\n'
            '  }\n'
            '}\n'
        )
        p = tmp_path / "clawdbot.json5"
        p.write_text(content, encoding="utf-8")
        adapter = self._adapter()
        with patch.object(adapter, "_resolve_path", return_value=p):
            config = adapter.read_config()
        assert "srv1" in config["mcpServers"]

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "clawdbot.json5"
        adapter = self._adapter()
        with patch.object(adapter, "_resolve_path", return_value=p):
            adapter.register_server("my-server", {"command": "node"})
            servers = adapter.discover_servers()
        assert "my-server" in servers


# ---------------------------------------------------------------------------
# Warp Terminal
# ---------------------------------------------------------------------------

class TestWarpAdapter:
    def _adapter(self):
        return get_adapter("warp")

    def test_config_path_linux(self) -> None:
        with patch("sys.platform", "linux"):
            from src.multimcp.adapters.tools.warp import WarpAdapter
            adapter = WarpAdapter()
            path = adapter.config_path()
        assert path is not None
        assert "mcp_servers.json" in str(path)

    def test_config_path_macos(self) -> None:
        with patch("sys.platform", "darwin"):
            from src.multimcp.adapters.tools.warp import WarpAdapter
            adapter = WarpAdapter()
            path = adapter.config_path()
        assert path is not None
        assert "2BBY89MBSN.dev.warp" in str(path)

    def test_register_linux_single_file(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_servers.json"
        adapter = self._adapter()
        with patch("sys.platform", "linux"), \
             patch.object(adapter, "config_path", return_value=p), \
             patch.object(adapter, "_is_dir_mode", return_value=False):
            adapter.register_server("test", {"command": "python"})
            servers = adapter.discover_servers()
        assert "test" in servers

    def test_register_dir_mode(self, tmp_path: Path) -> None:
        mcp_dir = tmp_path / "mcp"
        adapter = self._adapter()
        with patch("sys.platform", "darwin"), \
             patch.object(adapter, "config_path", return_value=mcp_dir), \
             patch.object(adapter, "_is_dir_mode", return_value=True):
            adapter.register_server("test-server", {"command": "node"})
            servers = adapter.discover_servers()
        assert "test-server" in servers
        assert (mcp_dir / "test-server.json").exists()


# ---------------------------------------------------------------------------
# JetBrains (Junie mcp.json)
# ---------------------------------------------------------------------------

class TestJetBrainsAdapter:
    def _adapter(self):
        return get_adapter("jetbrains")

    def test_config_path_returns_junie_mcp_json(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert ".junie" in str(path)
        assert path.name == "mcp.json"

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"command": "python"})
            servers = adapter.discover_servers()
        assert "srv" in servers
        assert servers["srv"]["command"] == "python"

    def test_discover_servers_returns_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            result = adapter.discover_servers()
        assert isinstance(result, dict)
        assert result == {}

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "mcp.json"):
            assert adapter.read_config() == {}


# ---------------------------------------------------------------------------
# TOML adapters (Codex CLI / Desktop / gptme)
# ---------------------------------------------------------------------------

class TestCodexCLIAdapter:
    def _adapter(self):
        return get_adapter("codex_cli")

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert ".codex" in str(path)
        assert path.name == "config.toml"

    def test_read_write_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv1", {"command": "python", "args": ["s.py"]})
            servers = adapter.discover_servers()
        assert "srv1" in servers
        assert servers["srv1"]["command"] == "python"

    def test_codex_desktop_shares_path(self) -> None:
        cli = get_adapter("codex_cli")
        desktop = get_adapter("codex_desktop")
        assert cli.config_path() == desktop.config_path()


class TestGptmeAdapter:
    def _adapter(self):
        return get_adapter("gptme")

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert "gptme" in str(path)
        assert path.name == "config.toml"

    def test_register_and_discover_servers(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("my-srv", {"url": "http://localhost:8080/sse"})
            servers = adapter.discover_servers()
        assert "my-srv" in servers
        assert servers["my-srv"]["url"] == "http://localhost:8080/sse"

    def test_register_replaces_existing_entry(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"url": "http://old"})
            adapter.register_server("srv", {"url": "http://new"})
            servers = adapter.discover_servers()
            raw = adapter.read_config()
        assert servers["srv"]["url"] == "http://new"
        # Should not have duplicates
        assert len([s for s in raw["mcp"]["servers"] if s["name"] == "srv"]) == 1


# ---------------------------------------------------------------------------
# Antigravity
# ---------------------------------------------------------------------------

class TestAntigravityAdapter:
    def _adapter(self):
        return get_adapter("antigravity")

    def test_config_path_is_gemini_antigravity(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert path.name == "mcp_config.json"
        assert ".gemini" in str(path)
        assert "antigravity" in str(path)

    def test_supported_on_all_platforms(self) -> None:
        adapter = self._adapter()
        assert set(adapter.supported_platforms) == {"macos", "linux", "windows"}

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "mcp_config.json"):
            assert adapter.read_config() == {}

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_config.json"
        adapter = self._adapter()
        cfg = {"command": "python", "args": ["server.py"], "env": {}}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("my-server", cfg)
            servers = adapter.discover_servers()
        assert "my-server" in servers
        assert servers["my-server"]["command"] == "python"

    def test_register_preserves_existing_servers(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"existing": {"command": "node"}}}
        p = _make_json_config(tmp_path, "mcp_config.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new", {"command": "python"})
            servers = adapter.discover_servers()
        assert "existing" in servers
        assert "new" in servers

    def test_write_config_creates_directories(self, tmp_path: Path) -> None:
        p = tmp_path / "subdir" / "mcp_config.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"command": "node"})
        assert p.exists()

    def test_discover_servers_empty_mcp_servers_key(self, tmp_path: Path) -> None:
        data = {"other_key": "value"}
        p = _make_json_config(tmp_path, "mcp_config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert servers == {}

    def test_file_has_trailing_newline(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp_config.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"command": "node"})
        content = p.read_text(encoding="utf-8")
        assert content.endswith("\n")


# ---------------------------------------------------------------------------
# GitHub Copilot CLI
# ---------------------------------------------------------------------------

class TestGitHubCopilotAdapter:
    def _adapter(self):
        return get_adapter("github_copilot")

    def test_config_path_is_copilot_mcp_config_json(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert path.name == "mcp-config.json"
        assert ".copilot" in str(path)

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "mcp-config.json"):
            assert adapter.read_config() == {}

    def test_uses_mcpservers_key(self, tmp_path: Path) -> None:
        data = {"mcpServers": {"my-srv": {"type": "stdio", "command": "python"}}}
        p = _make_json_config(tmp_path, "mcp-config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert "my-srv" in servers

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp-config.json"
        adapter = self._adapter()
        cfg = {"type": "stdio", "command": "uvx", "args": ["mcp-server-git"]}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("git-server", cfg)
            servers = adapter.discover_servers()
        assert "git-server" in servers
        assert servers["git-server"]["type"] == "stdio"
        assert servers["git-server"]["command"] == "uvx"

    def test_register_preserves_existing_servers(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"old": {"type": "stdio", "command": "node"}}}
        p = _make_json_config(tmp_path, "mcp-config.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new", {"type": "sse", "url": "http://localhost:9000"})
            servers = adapter.discover_servers()
        assert "old" in servers
        assert "new" in servers

    def test_register_updates_existing_server(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"my-srv": {"type": "stdio", "command": "old-cmd"}}}
        p = _make_json_config(tmp_path, "mcp-config.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("my-srv", {"type": "stdio", "command": "new-cmd"})
            servers = adapter.discover_servers()
        assert servers["my-srv"]["command"] == "new-cmd"

    def test_discover_no_mcpservers_key_returns_empty(self, tmp_path: Path) -> None:
        data = {"other_setting": True}
        p = _make_json_config(tmp_path, "mcp-config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            assert adapter.discover_servers() == {}

    def test_write_creates_copilot_directory(self, tmp_path: Path) -> None:
        p = tmp_path / ".copilot" / "mcp-config.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"type": "stdio", "command": "python"})
        assert p.exists()


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

class TestOpenCodeAdapter:
    def _adapter(self):
        return get_adapter("opencode")

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert "opencode" in str(path)

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "config.json"):
            assert adapter.read_config() == {}

    def test_uses_mcp_key_not_mcpservers(self, tmp_path: Path) -> None:
        data = {"mcp": {"my-srv": {"type": "local", "command": "python"}}}
        p = _make_json_config(tmp_path, "config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert "my-srv" in servers

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "config.json"
        adapter = self._adapter()
        cfg = {"type": "local", "command": "python", "args": ["main.py"], "env": {}}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("multi-mcp", cfg)
            servers = adapter.discover_servers()
        assert "multi-mcp" in servers
        assert servers["multi-mcp"]["type"] == "local"

    def test_register_preserves_existing_servers(self, tmp_path: Path) -> None:
        existing = {"mcp": {"existing": {"type": "remote", "url": "http://example.com"}}}
        p = _make_json_config(tmp_path, "config.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new", {"type": "local", "command": "python"})
            servers = adapter.discover_servers()
        assert "existing" in servers
        assert "new" in servers

    def test_discover_no_mcp_key_returns_empty(self, tmp_path: Path) -> None:
        data = {"other_key": "value"}
        p = _make_json_config(tmp_path, "config.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            assert adapter.discover_servers() == {}

    def test_supported_on_all_platforms(self) -> None:
        adapter = self._adapter()
        assert set(adapter.supported_platforms) == {"macos", "linux", "windows"}


# ---------------------------------------------------------------------------
# Raycast
# ---------------------------------------------------------------------------

class TestRaycastAdapter:
    def _adapter(self):
        return get_adapter("raycast")

    def test_supported_platforms(self) -> None:
        adapter = self._adapter()
        assert set(adapter.supported_platforms) == {"macos", "linux"}

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert path.name == "mcp.json"
        assert ".config" in str(path)
        assert "raycast" in str(path)

    def test_read_config_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "missing.json"):
            assert adapter.read_config() == {}

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("my-raycast-srv", {"command": "python", "args": ["s.py"]})
            servers = adapter.discover_servers()
        assert "my-raycast-srv" in servers
        assert servers["my-raycast-srv"]["command"] == "python"

    def test_discover_uses_mcpservers_key(self, tmp_path: Path) -> None:
        data = {"mcpServers": {"srv1": {"command": "node"}}}
        p = _make_json_config(tmp_path, "mcp.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert "srv1" in servers

    def test_is_not_supported_on_windows(self) -> None:
        adapter = self._adapter()
        with patch("src.multimcp.adapters.base.sys.platform", "win32"):
            assert not adapter.is_supported()


# ---------------------------------------------------------------------------
# Roo Code
# ---------------------------------------------------------------------------

class TestRooCodeAdapter:
    def _adapter(self):
        return get_adapter("roo_code")

    def test_config_path_is_roo_mcp_json(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert path.name == "mcp.json"
        assert ".roo" in str(path)

    def test_supported_on_all_platforms(self) -> None:
        adapter = self._adapter()
        assert set(adapter.supported_platforms) == {"macos", "linux", "windows"}

    def test_read_config_missing_returns_empty(self, tmp_path: Path) -> None:
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=tmp_path / "mcp.json"):
            assert adapter.read_config() == {}

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "mcp.json"
        adapter = self._adapter()
        cfg = {"command": "python", "args": ["s.py"], "disabled": False, "alwaysAllow": []}
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("roo-server", cfg)
            servers = adapter.discover_servers()
        assert "roo-server" in servers
        assert servers["roo-server"]["command"] == "python"
        assert servers["roo-server"]["disabled"] is False

    def test_register_preserves_existing_servers(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"old": {"command": "node", "alwaysAllow": ["tool1"]}}}
        p = _make_json_config(tmp_path, "mcp.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new", {"command": "python"})
            servers = adapter.discover_servers()
        assert "old" in servers
        assert "new" in servers

    def test_register_updates_existing_entry(self, tmp_path: Path) -> None:
        existing = {"mcpServers": {"srv": {"command": "old"}}}
        p = _make_json_config(tmp_path, "mcp.json", existing)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"command": "new"})
            servers = adapter.discover_servers()
        assert servers["srv"]["command"] == "new"

    def test_discover_no_mcpservers_key_returns_empty(self, tmp_path: Path) -> None:
        data = {"other_key": "value"}
        p = _make_json_config(tmp_path, "mcp.json", data)
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            assert adapter.discover_servers() == {}

    def test_write_creates_roo_directory(self, tmp_path: Path) -> None:
        p = tmp_path / ".roo" / "mcp.json"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("srv", {"command": "node"})
        assert p.exists()


# ---------------------------------------------------------------------------
# Codex Desktop (shares config with Codex CLI)
# ---------------------------------------------------------------------------

class TestCodexDesktopAdapter:
    def _adapter(self):
        return get_adapter("codex_desktop")

    def test_config_path(self) -> None:
        adapter = self._adapter()
        path = adapter.config_path()
        assert path is not None
        assert ".codex" in str(path)
        assert path.name == "config.toml"

    def test_register_and_discover(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("desktop-srv", {"command": "python", "args": ["s.py"]})
            servers = adapter.discover_servers()
        assert "desktop-srv" in servers
        assert servers["desktop-srv"]["command"] == "python"

    def test_preserves_existing_servers(self, tmp_path: Path) -> None:
        from src.multimcp.adapters._toml_helpers import write_toml
        p = tmp_path / "config.toml"
        write_toml(p, {"mcp_servers": {"existing": {"command": "node"}}})
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            adapter.register_server("new", {"command": "python"})
            servers = adapter.discover_servers()
        assert "existing" in servers
        assert "new" in servers

    def test_config_format_is_toml(self) -> None:
        adapter = self._adapter()
        assert adapter.config_format == "toml"

    def test_uses_mcp_servers_key(self, tmp_path: Path) -> None:
        from src.multimcp.adapters._toml_helpers import write_toml
        p = tmp_path / "config.toml"
        write_toml(p, {"mcp_servers": {"srv1": {"command": "node"}}})
        adapter = self._adapter()
        with patch.object(adapter, "config_path", return_value=p):
            servers = adapter.discover_servers()
        assert "srv1" in servers
        assert servers["srv1"]["command"] == "node"


# ---------------------------------------------------------------------------
# base.py: _current_platform
# ---------------------------------------------------------------------------

class TestCurrentPlatform:
    def test_darwin_maps_to_macos(self) -> None:
        from src.multimcp.adapters.base import _current_platform
        with patch("src.multimcp.adapters.base.sys.platform", "darwin"):
            assert _current_platform() == "macos"

    def test_win32_maps_to_windows(self) -> None:
        from src.multimcp.adapters.base import _current_platform
        with patch("src.multimcp.adapters.base.sys.platform", "win32"):
            assert _current_platform() == "windows"

    def test_linux_maps_to_linux(self) -> None:
        from src.multimcp.adapters.base import _current_platform
        with patch("src.multimcp.adapters.base.sys.platform", "linux"):
            assert _current_platform() == "linux"

    def test_other_platform_maps_to_linux(self) -> None:
        from src.multimcp.adapters.base import _current_platform
        with patch("src.multimcp.adapters.base.sys.platform", "freebsd"):
            assert _current_platform() == "linux"

    def test_is_supported_true_when_platform_in_list(self) -> None:
        adapter = get_adapter("claude_desktop")
        # claude_desktop supports all platforms; on any supported platform it returns True
        with patch("src.multimcp.adapters.base.sys.platform", "darwin"):
            assert adapter.is_supported() is True

    def test_is_supported_false_when_platform_not_in_list(self) -> None:
        adapter = get_adapter("raycast")  # supports ["macos", "linux"] but not windows
        with patch("src.multimcp.adapters.base.sys.platform", "win32"):
            assert adapter.is_supported() is False

# ---------------------------------------------------------------------------
# Backup mechanism (_backup helper + AdapterRegistry backup_dir propagation)
# ---------------------------------------------------------------------------

class TestBackupMechanism:
    """Tests for the .bak file creation that happens before any write_config call."""

    def _adapter(self):
        return get_adapter("claude_desktop")

    def test_backup_creates_bak_in_same_dir_by_default(self, tmp_path: Path) -> None:
        """When backup_dir is None, .bak lands beside the source file."""
        p = tmp_path / "claude_desktop_config.json"
        p.write_text('{"mcpServers": {}}', encoding="utf-8")
        adapter = self._adapter()
        adapter.backup_dir = None
        adapter._backup(p)
        bak = tmp_path / "claude_desktop_config.json.bak"
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == '{"mcpServers": {}}'

    def test_backup_uses_configured_backup_dir(self, tmp_path: Path) -> None:
        """When backup_dir is set, .bak is written there with a tool_name prefix.

        The prefix prevents collisions when multiple tools share the same
        config filename (e.g. ``settings.json``) in a common backup directory.
        """
        src_dir = tmp_path / "config"
        src_dir.mkdir()
        bak_dir = tmp_path / "backups"
        p = src_dir / "settings.json"
        p.write_text('{"data": 1}', encoding="utf-8")
        adapter = self._adapter()
        adapter.backup_dir = bak_dir
        adapter._backup(p)
        bak = bak_dir / "claude_desktop_settings.json.bak"
        assert bak.exists()
        assert not (src_dir / "settings.json.bak").exists()

    def test_backup_noop_when_file_missing(self, tmp_path: Path) -> None:
        """_backup is a no-op when the source file does not yet exist."""
        p = tmp_path / "nonexistent.json"
        adapter = self._adapter()
        adapter._backup(p)  # Should not raise
        assert not list(tmp_path.iterdir())

    def test_write_config_creates_bak_before_overwrite(self, tmp_path: Path) -> None:
        """Calling write_config on an existing file creates the .bak first."""
        p = tmp_path / "claude_desktop_config.json"
        original = {"mcpServers": {"old": {"command": "old"}}}
        p.write_text(json.dumps(original), encoding="utf-8")
        adapter = self._adapter()
        adapter.backup_dir = None
        with patch.object(adapter, "config_path", return_value=p):
            adapter.write_config({"mcpServers": {"new": {"command": "new"}}})
        bak = tmp_path / "claude_desktop_config.json.bak"
        assert bak.exists()
        assert json.loads(bak.read_text())["mcpServers"]["old"]["command"] == "old"

    def test_registry_propagates_backup_dir_to_adapters(self, tmp_path: Path) -> None:
        """AdapterRegistry(backup_dir=...) sets backup_dir on all adapter instances."""
        from src.multimcp.adapters.registry import AdapterRegistry
        bak_dir = tmp_path / "backups"
        registry = AdapterRegistry(backup_dir=bak_dir)
        for adapter in registry.all():
            assert adapter.backup_dir == bak_dir

    def test_registry_default_has_no_backup_dir(self) -> None:
        """AdapterRegistry() without backup_dir leaves backup_dir as None."""
        from src.multimcp.adapters.registry import AdapterRegistry
        registry = AdapterRegistry()
        for adapter in registry.all():
            assert adapter.backup_dir is None

    def test_configure_registry_applies_backup_dir(self, tmp_path: Path) -> None:
        """configure_registry() re-initialises the singleton with the given backup_dir."""
        from src.multimcp.adapters import configure_registry, list_adapters
        bak_dir = tmp_path / "baks"
        configure_registry(backup_dir=bak_dir)
        try:
            for adapter in list_adapters():
                assert adapter.backup_dir == bak_dir
        finally:
            configure_registry(backup_dir=None)  # reset to default


class TestYamlConfigBackupDir:
    """Tests for the backup_dir field in MultiMCPConfig."""

    def test_backup_dir_defaults_to_none(self) -> None:
        from src.multimcp.yaml_config import MultiMCPConfig
        cfg = MultiMCPConfig()
        assert cfg.backup_dir is None

    def test_backup_dir_round_trips_through_yaml(self, tmp_path: Path) -> None:
        from src.multimcp.yaml_config import MultiMCPConfig, save_config, load_config
        cfg = MultiMCPConfig(backup_dir="/tmp/backups")
        p = tmp_path / "servers.yaml"
        save_config(cfg, p)
        loaded = load_config(p)
        assert loaded.backup_dir == "/tmp/backups"
