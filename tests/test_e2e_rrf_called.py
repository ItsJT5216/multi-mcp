"""E2E test: weighted_rrf() is invoked at runtime when turn > 0.

Replaces V-01/V-03 claims: "pipeline.py → fusion.py: WIRED" (import-only check,
not runtime invocation) and "full Phase 3 adaptive loop is live" (turn counter
wired but RRF never called at runtime).

This test verifies that the Tier 1 scoring path actually calls weighted_rrf()
with real arguments when turn > 0 and both env_query and conv_query are populated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp import types

from src.multimcp.retrieval.base import ToolRetriever
from src.multimcp.retrieval.logging import NullLogger
from src.multimcp.retrieval.models import RetrievalConfig, ScoredTool, RetrievalContext
from src.multimcp.retrieval.session import SessionStateManager
from src.multimcp.retrieval.pipeline import RetrievalPipeline
from src.multimcp.mcp_proxy import ToolMapping


def _make_tool_registry(n: int = 5) -> dict:
    reg: dict[str, ToolMapping] = {}
    for i in range(n):
        key = f"srv__{i:02d}_t"
        tool = types.Tool(
            name=f"{i:02d}_t",
            description=f"Tool {i}",
            inputSchema={"type": "object", "properties": {}},
        )
        reg[key] = ToolMapping(server_name="srv", client=None, tool=tool)
    return reg


class MockBMXFRetriever(ToolRetriever):
    """Retriever that has a built env_index to satisfy _index_available()."""

    def __init__(self):
        self._env_index = object()  # non-None → _index_available() returns True
        self._nl_index = object()

    async def retrieve(self, context: RetrievalContext, candidates) -> list[ScoredTool]:
        return []


class TestRRFCalledOnTurnGt0:
    """weighted_rrf() is invoked at runtime when turn > 0."""

    @pytest.mark.anyio
    async def test_rrf_called_on_turn_gt_0(self):
        """weighted_rrf() is called when index available, both queries non-empty, turn > 0.

        This replaces V-01/V-03 verification. The test patches weighted_rrf in the
        pipeline module scope and confirms it's actually called during Tier 1 scoring.
        """
        registry = _make_tool_registry(8)
        config = RetrievalConfig(
            enabled=True,
            rollout_stage="shadow",
            shadow_mode=True,
            top_k=5,
            max_k=8,
        )
        session_manager = SessionStateManager(config)
        pipeline = RetrievalPipeline(
            retriever=MockBMXFRetriever(),
            session_manager=session_manager,
            logger=NullLogger(),
            config=config,
            tool_registry=registry,
        )

        # Pre-populate session evidence so env_query is non-empty
        from src.multimcp.retrieval.models import WorkspaceEvidence
        evidence = WorkspaceEvidence(
            workspace_hash="test-hash",
            workspace_confidence=0.8,
            merged_tokens={"lang:python": 2.0, "manifest:pyproject.toml": 3.0},
        )
        pipeline._session_evidence["rrf-session"] = evidence

        # Make the retriever return actual ScoredTool results
        scored_results = [
            ScoredTool(tool_key=k, tool_mapping=v, score=float(i + 1))
            for i, (k, v) in enumerate(list(registry.items())[:5])
        ]

        async def mock_retrieve(ctx, candidates):
            return scored_results

        pipeline.retriever.retrieve = mock_retrieve  # type: ignore[method-assign]

        # Patch weighted_rrf in pipeline module so we can verify it's called
        with patch("src.multimcp.retrieval.pipeline._weighted_rrf") as mock_rrf, \
             patch("src.multimcp.retrieval.pipeline._HAS_FUSION", True), \
             patch("src.multimcp.retrieval.pipeline._compute_alpha", return_value=0.5):
            mock_rrf.return_value = scored_results

            # Turn 1: conv_query populated, env_query populated (from evidence)
            # turn > 0 satisfied by first call (turn_number increments to 1)
            result = await pipeline.get_tools_for_list(
                "rrf-session",
                conversation_context="list python files search",
            )

        # weighted_rrf must have been called (Tier 1 scoring path executed)
        assert mock_rrf.called, (
            "weighted_rrf() must be called when index available and both queries non-empty "
            "(V-01/V-03 replacement: not just imported, actually invoked at runtime)"
        )

    @pytest.mark.anyio
    async def test_fusion_imported_and_has_fusion_flag_true(self):
        """weighted_rrf and compute_alpha are importable and _HAS_FUSION is True."""
        import src.multimcp.retrieval.pipeline as pipeline_module

        assert pipeline_module._HAS_FUSION is True, (
            "_HAS_FUSION must be True (fusion.py successfully imported)"
        )

        from src.multimcp.retrieval.fusion import weighted_rrf, compute_alpha
        import asyncio

        assert callable(weighted_rrf), "weighted_rrf must be callable"
        assert callable(compute_alpha), "compute_alpha must be callable"
