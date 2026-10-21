"""Retrieval pipeline for intelligent tool filtering and ranking."""

from .base import PassthroughRetriever, ToolRetriever
from .catalog import build_snapshot
from .logging import NullLogger, RetrievalLogger
from .models import RetrievalConfig, RetrievalContext, ScoredTool, ToolCatalogSnapshot, ToolDoc
from .rollout import get_session_group, is_canary_session
from .session import SessionStateManager

try:
    from .bmx_retriever import BMXFRetriever
except ImportError:  # pragma: no cover - dependency-light validation fallback
    BMXFRetriever = None  # type: ignore[assignment,misc]

try:
    from .pipeline import RetrievalPipeline
except ImportError:  # pragma: no cover - dependency-light validation fallback
    RetrievalPipeline = None  # type: ignore[assignment,misc]

__all__ = [
    "BMXFRetriever",
    "PassthroughRetriever",
    "NullLogger",
    "RetrievalConfig",
    "RetrievalContext",
    "RetrievalLogger",
    "RetrievalPipeline",
    "ScoredTool",
    "SessionStateManager",
    "ToolCatalogSnapshot",
    "ToolDoc",
    "ToolRetriever",
    "build_snapshot",
    "get_session_group",
    "is_canary_session",
]
