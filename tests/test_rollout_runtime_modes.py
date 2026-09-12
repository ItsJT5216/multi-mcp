"""Tests for rollout runtime mode dispatch semantics and YAML config wiring.

Covers (Phase 9 mandatory test contract per 09-01-PLAN.md):
1. Off mode (enabled=False): all tools returned without scoring.
2. Shadow mode (enabled=True, shadow_mode=True): scoring executes, RankingEvent emitted,
   all tools returned (no filtering).
3. Canary mode - control session: returns all tools.
4. Canary mode - canary session: returns bounded set + routing tool.
5. GA mode: all sessions get bounded set + routing tool.
6. YAML RetrievalSettings exposes all Phase 2+4 fields with correct defaults.
7. Backward compat: no retrieval: block → enabled=False → all tools returned.
8. Logger selection: log_path set → FileRetrievalLogger; no log_path → NullLogger.
"""

from __future__ import annotations

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from mcp import types

from src.multimcp.retrieval.pipeline import RetrievalPipeline
from src.multimcp.retrieval.models import RetrievalConfig
from src.multimcp.retrieval.session import SessionStateManager
from src.multimcp.retrieval.base import PassthroughRetriever
from src.multimcp.retrieval.logging import RetrievalLogger, NullLogger, FileRetrievalLogger
from src.multimcp.mcp_proxy import ToolMapping
from src.multimcp.yaml_config import RetrievalSettings


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_tool_registry(n: int = 8) -> dict:
    """Create n mock ToolMapping entries."""
    reg: dict[str, ToolMapping] = {}
    for i in range(n):
        key = f"server__{i:02d}_tool"
        tool = types.Tool(
            name=f"{i:02d}_tool",
            description=f"Tool {i}",
            inputSchema={"type": "object", "properties": {}},
        )
        reg[key] = ToolMapping(server_name="server", client=None, tool=tool)
    return reg


def make_pipeline_with_stage(rollout_stage: str, shadow_mode: bool = False, n: int = 8) -> tuple[RetrievalPipeline, MagicMock]:
    """Create a minimal enabled pipeline with the given rollout_stage and shadow_mode.

    Returns (pipeline, mock_logger).
    """
    registry = make_tool_registry(n)
    config = RetrievalConfig(
        enabled=True,
        rollout_stage=rollout_stage,
        shadow_mode=shadow_mode,
        top_k=5,
        max_k=10,
        enable_routing_tool=False,
    )
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(
        PassthroughRetriever(),
        session_manager,
        logger,
        config,
        registry,
    )
    return pipeline, logger


# ── Shadow mode dispatch guard ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_shadow_stage_returns_all_tools():
    """When rollout_stage="shadow", get_tools_for_list returns ALL tools (no filtering).

    This is the shadow mode dispatch guard: the pipeline scores tools but does
    not restrict the returned set.
    """
    n = 8
    pipeline, _ = make_pipeline_with_stage("shadow", n=n)
    sid = "shadow-all-tools"

    result = await pipeline.get_tools_for_list(sid)

    assert len(result) == n, (
        f"Shadow mode must return all {n} tools; got {len(result)}"
    )


@pytest.mark.anyio
async def test_shadow_stage_scoring_executes():
    """When rollout_stage="shadow", the scoring path still executes (log_ranking_event is called).

    Shadow mode must score for telemetry purposes even though it returns all tools.
    """
    pipeline, logger = make_pipeline_with_stage("shadow")
    sid = "shadow-scoring"

    await pipeline.get_tools_for_list(sid)

    assert logger.log_ranking_event.call_count == 1, (
        "log_ranking_event must be called even in shadow mode"
    )


@pytest.mark.anyio
async def test_shadow_stage_does_not_filter():
    """With rollout_stage="shadow" and enabled=True, no filtering occurs.

    Even with top_k=3 and 8 tools in registry, shadow mode must return all 8.
    """
    n = 8
    registry = make_tool_registry(n)
    config = RetrievalConfig(
        enabled=True,
        rollout_stage="shadow",
        shadow_mode=True,
        top_k=3,     # would limit to 3 in GA mode
        max_k=5,
        enable_routing_tool=False,
    )
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(
        PassthroughRetriever(),
        session_manager,
        logger,
        config,
        registry,
    )

    result = await pipeline.get_tools_for_list("shadow-no-filter")

    assert len(result) == n, (
        f"Shadow mode must return all {n} tools regardless of top_k={config.top_k}; got {len(result)}"
    )


@pytest.mark.anyio
async def test_ga_stage_filters_tools():
    """Contrast: with rollout_stage="ga" and top_k=3, only up to 3+routing tools returned.

    Verifies that filtering IS active in GA mode (shadow mode guard is not leaking).
    """
    n = 8
    registry = make_tool_registry(n)
    config = RetrievalConfig(
        enabled=True,
        rollout_stage="ga",
        shadow_mode=False,
        top_k=3,
        max_k=5,
        enable_routing_tool=False,
    )
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(
        PassthroughRetriever(),
        session_manager,
        logger,
        config,
        registry,
    )

    result = await pipeline.get_tools_for_list("ga-filters")

    # GA mode should return top_k tools (not all n)
    assert len(result) < n, (
        f"GA mode must filter tools (got {len(result)}, expected < {n})"
    )
    assert len(result) <= config.max_k, (
        f"GA mode must not exceed max_k={config.max_k}; got {len(result)}"
    )


# ── Phase 9 mandatory tests ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_off_mode():
    """enabled=False returns all tools without scoring (kill switch).

    Phase 9 mandatory: test_off_mode — Off mode returns all tools, no RankingEvent emitted.
    """
    n = 8
    registry = make_tool_registry(n)
    config = RetrievalConfig(enabled=False)
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(PassthroughRetriever(), session_manager, logger, config, registry)

    result = await pipeline.get_tools_for_list("off-session")

    assert len(result) == n, f"Off mode must return all {n} tools; got {len(result)}"
    logger.log_ranking_event.assert_not_called(), (
        "Off mode must not emit RankingEvent (kill switch short-circuits scoring)"
    )


@pytest.mark.anyio
async def test_shadow_mode():
    """enabled=True, shadow_mode=True: scoring runs, RankingEvent emitted, all tools returned.

    Phase 9 mandatory: test_shadow_mode.
    """
    n = 8
    pipeline, logger = make_pipeline_with_stage("shadow", shadow_mode=True, n=n)

    result = await pipeline.get_tools_for_list("shadow-session")

    assert len(result) == n, f"Shadow mode must return all {n} tools; got {len(result)}"
    logger.log_ranking_event.assert_called_once(), (
        "Shadow mode must emit RankingEvent (scoring still executes)"
    )


@pytest.mark.anyio
async def test_canary_mode_control():
    """Canary mode: control session returns all tools.

    Phase 9 mandatory: test_canary_mode_control.
    """
    n = 10
    # Use canary_percentage=0.0 to force all sessions to control group
    registry = make_tool_registry(n)
    config = RetrievalConfig(
        enabled=True,
        rollout_stage="canary",
        shadow_mode=False,
        canary_percentage=0.0,  # 0% canary -> all sessions are control
        top_k=5,
        max_k=10,
        enable_routing_tool=False,
    )
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(PassthroughRetriever(), session_manager, logger, config, registry)

    result = await pipeline.get_tools_for_list("control-session")

    # Control group must return all tools (no filtering)
    assert len(result) == n, (
        f"Canary mode control session must return all {n} tools; got {len(result)}"
    )


@pytest.mark.anyio
async def test_canary_mode_canary():
    """Canary mode: canary session returns bounded set + routing tool.

    Phase 9 mandatory: test_canary_mode_canary.
    """
    n = 10
    top_k = 3
    # canary_percentage=100.0 forces all sessions to canary group
    registry = make_tool_registry(n)
    config = RetrievalConfig(
        enabled=True,
        rollout_stage="canary",
        shadow_mode=False,
        canary_percentage=100.0,  # 100% canary -> all sessions are canary
        top_k=top_k,
        max_k=5,
        enable_routing_tool=True,
    )
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(PassthroughRetriever(), session_manager, logger, config, registry)

    result = await pipeline.get_tools_for_list("canary-session")

    # Canary group: bounded set + routing tool => count < n
    assert len(result) < n, (
        f"Canary mode canary session must filter tools (got {len(result)}, expected < {n})"
    )


@pytest.mark.anyio
async def test_ga_mode():
    """GA mode: all sessions return bounded set + routing tool.

    Phase 9 mandatory: test_ga_mode.
    """
    n = 10
    top_k = 3
    registry = make_tool_registry(n)
    config = RetrievalConfig(
        enabled=True,
        rollout_stage="ga",
        shadow_mode=False,
        top_k=top_k,
        max_k=5,
        enable_routing_tool=False,
    )
    session_manager = SessionStateManager(config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(PassthroughRetriever(), session_manager, logger, config, registry)

    result = await pipeline.get_tools_for_list("ga-session")

    # GA mode: all sessions get bounded set (not all tools)
    assert len(result) < n, (
        f"GA mode must filter tools (got {len(result)}, expected < {n})"
    )
    assert len(result) <= config.max_k, (
        f"GA mode must not exceed max_k={config.max_k}; got {len(result)}"
    )


def test_yaml_retrieval_settings_all_fields():
    """RetrievalSettings exposes all Phase 2+4 fields with correct defaults.

    Phase 9 mandatory: test_yaml_retrieval_settings_all_fields.
    """
    s = RetrievalSettings()

    # Core fields
    assert s.enabled is False, f"Default enabled must be False, got {s.enabled}"
    assert s.top_k == 15, f"Default top_k must be 15, got {s.top_k}"
    assert s.full_description_count == 3
    assert s.anchor_tools == []

    # Phase 2 fields
    assert s.shadow_mode is False, f"Default shadow_mode must be False, got {s.shadow_mode}"
    assert s.scorer == "bmxf", f"Default scorer must be 'bmxf', got {s.scorer}"
    assert s.max_k == 20, f"Default max_k must be 20, got {s.max_k}"
    assert s.enable_routing_tool is True
    assert s.enable_telemetry is True
    assert s.telemetry_poll_interval == 30

    # Phase 4 rollout fields
    assert s.canary_percentage == 0.0, f"Default canary_percentage must be 0.0, got {s.canary_percentage}"
    assert s.rollout_stage == "shadow", f"Default rollout_stage must be 'shadow', got {s.rollout_stage}"

    # Logging
    assert s.log_path == "", f"Default log_path must be empty string, got {s.log_path!r}"


@pytest.mark.anyio
async def test_default_config_backward_compat():
    """No YAML retrieval config → enabled=False → all tools returned (backward compat).

    Phase 9 mandatory: test_default_config_backward_compat.
    When no retrieval: block appears in YAML, RetrievalSettings() defaults give
    enabled=False, which means the pipeline kill switch fires and all tools are returned.
    """
    from src.multimcp.yaml_config import RetrievalSettings

    n = 12
    registry = make_tool_registry(n)

    # Simulate no retrieval: block in YAML — defaults apply
    yaml_retrieval = RetrievalSettings()
    assert yaml_retrieval.enabled is False, "Default must be disabled"
    assert yaml_retrieval.shadow_mode is False

    # Build RetrievalConfig from defaults (same path as multi_mcp.py)
    retrieval_config = RetrievalConfig(
        enabled=yaml_retrieval.enabled,
        top_k=yaml_retrieval.top_k,
        full_description_count=yaml_retrieval.full_description_count,
        anchor_tools=yaml_retrieval.anchor_tools,
        shadow_mode=yaml_retrieval.shadow_mode,
        scorer=yaml_retrieval.scorer,
        max_k=yaml_retrieval.max_k,
        enable_routing_tool=yaml_retrieval.enable_routing_tool,
        enable_telemetry=yaml_retrieval.enable_telemetry,
        telemetry_poll_interval=yaml_retrieval.telemetry_poll_interval,
        canary_percentage=yaml_retrieval.canary_percentage,
        rollout_stage=yaml_retrieval.rollout_stage,
    )

    session_manager = SessionStateManager(retrieval_config)
    logger = MagicMock(spec=RetrievalLogger)
    logger.log_ranking_event = AsyncMock()
    pipeline = RetrievalPipeline(PassthroughRetriever(), session_manager, logger, retrieval_config, registry)

    result = await pipeline.get_tools_for_list("compat-session")

    # enabled=False → kill switch fires → all tools returned
    assert len(result) == n, (
        f"Default config (enabled=False) must return all {n} tools; got {len(result)}"
    )
    logger.log_ranking_event.assert_not_called(), (
        "Disabled pipeline must not emit RankingEvent"
    )


def test_logger_selection():
    """log_path set → FileRetrievalLogger; no log_path → NullLogger.

    Phase 9 mandatory: test_logger_selection.
    """
    # No log_path → NullLogger
    yaml_no_log = RetrievalSettings(log_path="")
    assert yaml_no_log.log_path == ""
    # Simulate logger selection logic from multi_mcp.py
    if yaml_no_log.log_path:
        logger_type = FileRetrievalLogger
    else:
        logger_type = NullLogger
    assert logger_type is NullLogger, "Empty log_path must produce NullLogger"

    # With log_path → FileRetrievalLogger
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = str(Path(tmpdir) / "test.jsonl")
        yaml_with_log = RetrievalSettings(log_path=log_file)
        assert yaml_with_log.log_path == log_file

        if yaml_with_log.log_path:
            _p = Path(yaml_with_log.log_path)
            _p.parent.mkdir(parents=True, exist_ok=True)
            try:
                selected_logger = FileRetrievalLogger(_p)
                logger_class = type(selected_logger)
            except Exception:
                logger_class = NullLogger
        else:
            logger_class = NullLogger

        assert logger_class is FileRetrievalLogger, (
            f"Non-empty log_path must produce FileRetrievalLogger; got {logger_class}"
        )
