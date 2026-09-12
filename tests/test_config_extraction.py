"""Tests for MultiMCP._extract_mcp_servers config-format extraction.

Covers all four supported formats and, critically, the interaction between
bare-format configs (every top-level key is a server name) and sibling
settings blocks such as the top-level ``retrieval`` key that
``_build_config_from_json_file`` parses into RetrievalSettings.

Regression guard for: bare-format extraction previously returned ALL
top-level dicts once any one entry looked like a server, so a config like
``{"weather": {"command": ...}, "retrieval": {"enabled": true}}`` produced a
bogus server named "retrieval" (command=None) that failed to connect at
startup.
"""
import json

import pytest

from src.multimcp.multi_mcp import MultiMCP


class TestExtractMcpServersFormats:
    """Existing supported formats keep working (no regression)."""

    def test_mcp_servers_key(self):
        data = {"mcpServers": {"weather": {"command": "python", "args": ["w.py"]}}}
        servers = MultiMCP._extract_mcp_servers(data)
        assert set(servers) == {"weather"}
        assert servers["weather"]["command"] == "python"

    def test_servers_key(self):
        data = {"servers": {"k8s": {"url": "http://127.0.0.1:9080/sse"}}}
        servers = MultiMCP._extract_mcp_servers(data)
        assert set(servers) == {"k8s"}

    def test_mcp_key(self):
        data = {"mcp": {"gem": {"command": "npx", "args": ["-y", "gem"]}}}
        servers = MultiMCP._extract_mcp_servers(data)
        assert set(servers) == {"gem"}

    def test_bare_format_all_server_entries(self):
        data = {
            "weather": {"command": "python", "args": ["w.py"]},
            "remote": {"url": "http://127.0.0.1:9080/sse"},
        }
        servers = MultiMCP._extract_mcp_servers(data)
        assert set(servers) == {"weather", "remote"}

    def test_empty_data_returns_empty(self):
        assert MultiMCP._extract_mcp_servers({}) == {}

    def test_no_server_like_entries_returns_empty(self):
        data = {"retrieval": {"enabled": True}, "profiles": {"dev": {}}}
        assert MultiMCP._extract_mcp_servers(data) == {}

    def test_command_as_list_normalized(self):
        data = {"mcpServers": {"srv": {"command": ["python", "s.py", "--x"]}}}
        servers = MultiMCP._extract_mcp_servers(data)
        assert servers["srv"]["command"] == "python"
        assert servers["srv"]["args"] == ["s.py", "--x"]


class TestBareFormatExcludesSettingsBlocks:
    """Bare format must not misparse sibling settings dicts as servers."""

    def test_retrieval_block_not_a_server(self):
        data = {
            "weather": {"command": "python", "args": ["w.py"]},
            "retrieval": {"enabled": True, "top_k": 10, "scorer": "bmxf"},
        }
        servers = MultiMCP._extract_mcp_servers(data)
        assert "retrieval" not in servers
        assert set(servers) == {"weather"}

    def test_mixed_settings_blocks_excluded(self):
        data = {
            "srv_a": {"command": "python"},
            "srv_b": {"url": "http://localhost:9080/sse"},
            "retrieval": {"enabled": True},
            "notes": {"comment": "not a server"},
        }
        servers = MultiMCP._extract_mcp_servers(data)
        assert set(servers) == {"srv_a", "srv_b"}

    def test_mcp_servers_key_with_retrieval_sibling_unaffected(self):
        """The documented format: retrieval sits beside mcpServers."""
        data = {
            "mcpServers": {"weather": {"command": "python"}},
            "retrieval": {"enabled": True, "top_k": 5},
        }
        servers = MultiMCP._extract_mcp_servers(data)
        assert set(servers) == {"weather"}


class TestBuildConfigFromJsonWithRetrieval:
    """End-to-end: bare-format JSON config with retrieval settings."""

    @pytest.fixture
    def bare_config_file(self, tmp_path):
        cfg = {
            "weather": {"command": "python", "args": ["w.py"]},
            "retrieval": {"enabled": True, "top_k": 7, "scorer": "keyword"},
        }
        p = tmp_path / "mcp.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return p

    def test_retrieval_parsed_as_settings_not_server(self, bare_config_file):
        mcp = MultiMCP(config=str(bare_config_file))
        config = mcp._build_config_from_json_file()
        # retrieval must land in config.retrieval, never in config.servers
        assert set(config.servers) == {"weather"}
        assert "retrieval" not in config.servers
        assert config.retrieval.enabled is True
        assert config.retrieval.top_k == 7
        assert config.retrieval.scorer == "keyword"

    def test_invalid_retrieval_settings_raise(self, tmp_path):
        cfg = {
            "mcpServers": {"weather": {"command": "python"}},
            "retrieval": {"enabled": True, "scorer": "not-a-scorer"},
        }
        p = tmp_path / "mcp.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        mcp = MultiMCP(config=str(p))
        with pytest.raises(RuntimeError, match="Invalid retrieval settings"):
            mcp._build_config_from_json_file()
