"""Bootstrap-level retrieval config tests for the real MultiMCP.run() path.

These tests intentionally drive ``MultiMCP.run()`` through the retrieval
pipeline bootstrap block with patched collaborators so they verify the real
runtime wiring instead of reconstructing ``RetrievalConfig`` or
``RetrievalPipeline`` directly inside the test body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import types

from src.multimcp.mcp_proxy import ToolMapping
from src.multimcp.yaml_config import MultiMCPConfig, RetrievalSettings


def _make_registry(n: int = 2) -> dict[str, ToolMapping]:
    registry: dict[str, ToolMapping] = {}
    for i in range(n):
        tool = types.Tool(
            name=f"tool_{i}",
            description=f"Tool {i}",
            inputSchema={"type": "object", "properties": {}},
        )
        registry[f"server__tool_{i}"] = ToolMapping(
            server_name="server",
            client=None,
            tool=tool,
        )
    return registry


@dataclass
class BootstrapCapture:
    pipeline_kwargs: dict | None = None
    load_tools_from_yaml_arg: MultiMCPConfig | None = None
    retriever_calls: list[tuple[str, object | None]] = field(default_factory=list)
    logger_calls: list[tuple[str, Path | None]] = field(default_factory=list)
    rolling_metrics_windows: list[int] = field(default_factory=list)
    rebuild_index_calls: list[tuple[str, dict[str, ToolMapping]]] = field(default_factory=list)


class _FakeLoop:
    def add_signal_handler(self, *args, **kwargs) -> None:
        return None


class _FakeRetrieverBase:
    kind = "base"

    def __init__(self, capture: BootstrapCapture, config=None) -> None:
        self.capture = capture
        self.config = config
        self.capture.retriever_calls.append((self.kind, config))

    def rebuild_index(self, registry: dict[str, ToolMapping]) -> None:
        self.capture.rebuild_index_calls.append((self.kind, registry))


class _FakeBMXFRetriever(_FakeRetrieverBase):
    kind = "bmxf"

    def __init__(self, capture: BootstrapCapture, config) -> None:
        super().__init__(capture, config=config)


class _FakeKeywordRetriever(_FakeRetrieverBase):
    kind = "keyword"

    def __init__(self, capture: BootstrapCapture, config) -> None:
        super().__init__(capture, config=config)


class _FakePassthroughRetriever(_FakeRetrieverBase):
    kind = "passthrough"

    def __init__(self, capture: BootstrapCapture) -> None:
        super().__init__(capture, config=None)


class _FakeNullLogger:
    def __init__(self, capture: BootstrapCapture) -> None:
        capture.logger_calls.append(("null", None))


class _FakeFileRetrievalLogger:
    def __init__(self, capture: BootstrapCapture, log_path: Path) -> None:
        capture.logger_calls.append(("file", Path(log_path)))
        self.log_path = Path(log_path)


class _FakeRollingMetrics:
    def __init__(self, capture: BootstrapCapture, window_seconds: int = 1800) -> None:
        capture.rolling_metrics_windows.append(window_seconds)
        self.window_seconds = window_seconds


class _FakeRetrievalPipeline:
    def __init__(self, capture: BootstrapCapture, **kwargs) -> None:
        capture.pipeline_kwargs = kwargs
        self.kwargs = kwargs

    def rebuild_catalog(self, tool_registry: dict[str, ToolMapping]) -> None:
        self.rebuilt_catalog = tool_registry


class _FakeProxy:
    def __init__(self, capture: BootstrapCapture, tool_to_server: dict[str, ToolMapping]) -> None:
        self.capture = capture
        self.tool_to_server = tool_to_server
        self.retrieval_pipeline = None

    async def _on_server_disconnected(self, *args, **kwargs) -> None:
        return None

    def load_tools_from_yaml(self, yaml_config: MultiMCPConfig) -> None:
        self.capture.load_tools_from_yaml_arg = yaml_config

    async def initialize_single_client(self, *args, **kwargs) -> None:
        return None

    async def _send_tools_list_changed(self) -> None:
        return None


async def _run_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    retrieval_settings: RetrievalSettings,
    *,
    tool_registry: dict[str, ToolMapping] | None = None,
):
    import src.multimcp.multi_mcp as multi_mcp_module
    import src.multimcp.retrieval.base as base_module
    import src.multimcp.retrieval.bmx_retriever as bmx_module
    import src.multimcp.retrieval.keyword as keyword_module
    import src.multimcp.retrieval.logging as logging_module
    import src.multimcp.retrieval.metrics as metrics_module
    import src.multimcp.retrieval.pipeline as pipeline_module
    import src.multimcp.retrieval.telemetry.scanner as scanner_module

    capture = BootstrapCapture()
    yaml_config = MultiMCPConfig(retrieval=retrieval_settings)
    proxy = _FakeProxy(capture, tool_registry or _make_registry())
    telemetry_scanner = object()

    async def fake_bootstrap_from_yaml(self, yaml_path):
        return yaml_config

    async def fake_create(client_manager):
        return proxy

    monkeypatch.setattr(
        multi_mcp_module.MultiMCP,
        "_bootstrap_from_yaml",
        fake_bootstrap_from_yaml,
    )
    monkeypatch.setattr(
        multi_mcp_module.MCPProxyServer,
        "create",
        staticmethod(fake_create),
    )
    monkeypatch.setattr(
        multi_mcp_module.asyncio,
        "get_running_loop",
        lambda: _FakeLoop(),
    )

    monkeypatch.setattr(
        pipeline_module,
        "RetrievalPipeline",
        lambda **kwargs: _FakeRetrievalPipeline(capture, **kwargs),
    )
    monkeypatch.setattr(
        bmx_module,
        "BMXFRetriever",
        lambda config: _FakeBMXFRetriever(capture, config),
    )
    monkeypatch.setattr(
        keyword_module,
        "KeywordRetriever",
        lambda config: _FakeKeywordRetriever(capture, config),
    )
    monkeypatch.setattr(
        base_module,
        "PassthroughRetriever",
        lambda: _FakePassthroughRetriever(capture),
    )
    monkeypatch.setattr(
        logging_module,
        "NullLogger",
        lambda: _FakeNullLogger(capture),
    )
    monkeypatch.setattr(
        logging_module,
        "FileRetrievalLogger",
        lambda log_path: _FakeFileRetrievalLogger(capture, log_path),
    )
    monkeypatch.setattr(
        metrics_module,
        "RollingMetrics",
        lambda window_seconds=1800: _FakeRollingMetrics(capture, window_seconds),
    )
    monkeypatch.setattr(
        scanner_module,
        "TelemetryScanner",
        lambda: telemetry_scanner,
    )

    app = multi_mcp_module.MultiMCP(transport="stdio")
    app.client_manager.start_idle_checker = AsyncMock(return_value=None)
    app.client_manager.start_always_on_watchdog = AsyncMock(return_value=None)
    app.client_manager.close = AsyncMock(return_value=None)
    app.start_server = AsyncMock(return_value=None)

    await app.run()

    return app, capture, yaml_config, proxy, telemetry_scanner


class TestConfigDrivenPipelineInit:
    """Validates retrieval pipeline bootstrap wiring from YAML retrieval settings."""

    @pytest.mark.anyio
    async def test_run_builds_retrieval_pipeline_from_yaml_retrieval_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        retrieval = RetrievalSettings(
            enabled=True,
            top_k=7,
            full_description_count=2,
            anchor_tools=["server__tool_0"],
            shadow_mode=True,
            scorer="keyword",
            max_k=9,
            enable_routing_tool=False,
            enable_telemetry=False,
            telemetry_poll_interval=45,
            canary_percentage=12.5,
            rollout_stage="canary",
        )

        _, capture, yaml_config, proxy, telemetry_scanner = await _run_bootstrap(
            monkeypatch,
            retrieval,
        )

        pipeline_kwargs = capture.pipeline_kwargs
        assert pipeline_kwargs is not None
        assert capture.load_tools_from_yaml_arg is yaml_config
        assert proxy.retrieval_pipeline is not None

        config = pipeline_kwargs["config"]
        assert config.enabled is True
        assert config.top_k == retrieval.top_k
        assert config.full_description_count == retrieval.full_description_count
        assert config.anchor_tools == retrieval.anchor_tools
        assert config.shadow_mode is True
        assert config.scorer == retrieval.scorer
        assert config.max_k == retrieval.max_k
        assert config.enable_routing_tool is retrieval.enable_routing_tool
        assert config.enable_telemetry is retrieval.enable_telemetry
        assert config.telemetry_poll_interval == retrieval.telemetry_poll_interval
        assert config.canary_percentage == retrieval.canary_percentage
        assert config.rollout_stage == retrieval.rollout_stage
        assert pipeline_kwargs["tool_registry"] is proxy.tool_to_server
        assert pipeline_kwargs["telemetry_scanner"] is telemetry_scanner

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("scorer", "expected_retriever_kind"),
        [
            ("bmxf", "bmxf"),
            ("keyword", "keyword"),
        ],
    )
    async def test_run_selects_retriever_from_yaml_scorer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        scorer: str,
        expected_retriever_kind: str,
    ) -> None:
        _, capture, _, _, _ = await _run_bootstrap(
            monkeypatch,
            RetrievalSettings(enabled=True, scorer=scorer),
        )

        pipeline_kwargs = capture.pipeline_kwargs
        assert pipeline_kwargs is not None
        assert pipeline_kwargs["retriever"].kind == expected_retriever_kind

    def test_invalid_scorer_raises_validation_error(self) -> None:
        """An unrecognised scorer value is rejected at config parse time."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="scorer"):
            RetrievalSettings(enabled=True, scorer="unknown-scorer")

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("log_path", "expected_logger_kind"),
        [
            ("", "null"),
            ("logs/retrieval/runtime.jsonl", "file"),
        ],
    )
    async def test_run_selects_logger_from_yaml_log_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        log_path: str,
        expected_logger_kind: str,
    ) -> None:
        resolved_log_path = ""
        if log_path:
            resolved_log_path = str(tmp_path / log_path)

        _, capture, _, _, _ = await _run_bootstrap(
            monkeypatch,
            RetrievalSettings(enabled=True, log_path=resolved_log_path),
        )

        pipeline_kwargs = capture.pipeline_kwargs
        assert pipeline_kwargs is not None
        assert capture.logger_calls[0][0] == expected_logger_kind
        if expected_logger_kind == "file":
            assert capture.logger_calls[0][1] == Path(resolved_log_path)
            assert pipeline_kwargs["logger"].log_path == Path(resolved_log_path)

    @pytest.mark.anyio
    @pytest.mark.parametrize("enabled", [True, False])
    async def test_run_wires_rolling_metrics_only_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        enabled: bool,
    ) -> None:
        _, capture, _, _, _ = await _run_bootstrap(
            monkeypatch,
            RetrievalSettings(enabled=enabled),
        )

        pipeline_kwargs = capture.pipeline_kwargs
        assert pipeline_kwargs is not None

        if enabled:
            assert capture.rolling_metrics_windows == [1800]
            assert pipeline_kwargs["rolling_metrics"].window_seconds == 1800
        else:
            assert capture.rolling_metrics_windows == []
            assert pipeline_kwargs["rolling_metrics"] is None

    def test_no_hardcoded_shadow_bootstrap_in_multi_mcp(self) -> None:
        import src.multimcp.multi_mcp as multi_mcp_module

        assert not hasattr(multi_mcp_module, "_make_startup_retrieval_config"), (
            "_make_startup_retrieval_config() must not exist after Phase 9."
        )
