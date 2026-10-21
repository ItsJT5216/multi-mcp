"""RetrievalPipeline — single entry point for tool filtering and ranking."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from mcp import types

from .base import ToolRetriever
from .logging import RetrievalLogger
from .metrics import RollingMetrics
from .models import (
    RankingEvent,
    RetrievalConfig,
    RetrievalContext,
    ScoredTool,
    SessionRoutingState,
    WorkspaceEvidence,
)
from .session import SessionStateManager

# Optional imports — these are injected when wiring is complete
try:
    from .fusion import weighted_rrf as _weighted_rrf, compute_alpha as _compute_alpha  # noqa: F401
    _HAS_FUSION = True
except ImportError:
    _HAS_FUSION = False

try:
    from .routing_tool import build_routing_tool_schema
    _HAS_ROUTING_TOOL = True
except ImportError:
    _HAS_ROUTING_TOOL = False
    build_routing_tool_schema = None  # type: ignore[assignment]

try:
    from .rollout import get_session_group
except ImportError:
    def get_session_group(session_id: str, config: "RetrievalConfig") -> str:  # type: ignore[misc]
        return "control"

try:
    from .static_categories import STATIC_CATEGORIES, TIER6_NAMESPACE_PRIORITY
    _HAS_STATIC_CATEGORIES = True
except ImportError:
    _HAS_STATIC_CATEGORIES = False
    STATIC_CATEGORIES = {}  # type: ignore[assignment]
    TIER6_NAMESPACE_PRIORITY = []  # type: ignore[assignment]

try:
    from .telemetry.scanner import TelemetryScanner
    _HAS_TELEMETRY = True
except ImportError:
    _HAS_TELEMETRY = False
    TelemetryScanner = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from src.multimcp.mcp_proxy import ToolMapping
    from .ranker import RelevanceRanker
    from .assembler import TieredAssembler


# ── Conversation term extraction ─────────────────────────────────────────────

_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be",
    "to", "of", "and", "in", "for", "on", "with",
    "true", "false", "null", "none",
})

_ACTION_VERB_LEXICON: dict[str, list[str]] = {
    "list": ["get", "fetch", "show", "enumerate"],
    "create": ["add", "new", "make", "insert"],
    "search": ["find", "query", "lookup"],
    "delete": ["remove", "destroy", "drop"],
    "update": ["edit", "modify", "change", "patch"],
    "run": ["execute", "invoke", "start"],
    "get": ["fetch", "read", "retrieve"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _extract_conv_terms(raw: str) -> str:
    """Apply deterministic extraction pipeline to raw conversation context.

    Steps (in order):
    1. Lowercase
    2. Replace _ and - with spaces
    3. Tokenize with [a-z0-9]+
    4. Remove stopwords
    5. Deduplicate (first occurrence wins)
    6. Generate adjacent non-stopword bigrams
    7. Expand action verbs from lexicon
    8. Deduplicate final list
    9. Return space-joined result
    """
    text = raw.lower().replace("_", " ").replace("-", " ")
    tokens = _TOKEN_RE.findall(text)

    # Remove stopwords and deduplicate
    clean: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        if t not in _STOPWORDS and t not in seen:
            seen.add(t)
            clean.append(t)

    # Adjacent bigrams from clean tokens
    bigrams: list[str] = []
    for i in range(len(clean) - 1):
        bigram = clean[i] + " " + clean[i + 1]
        bigrams.append(bigram)

    combined = clean + bigrams

    # Action verb expansion
    expansions: list[str] = []
    for t in clean:
        if t in _ACTION_VERB_LEXICON:
            expansions.extend(_ACTION_VERB_LEXICON[t])

    combined.extend(expansions)

    # Final dedup preserving first occurrence
    final: list[str] = []
    final_seen: set[str] = set()
    for t in combined:
        if t not in final_seen:
            final_seen.add(t)
            final.append(t)

    return " ".join(final)


class RetrievalPipeline:
    """Orchestrates tool retrieval, ranking, and session state.

    When disabled: returns all tools (backward compatible).
    When enabled: returns session's active tool set (anchors + disclosed).
    """

    def __init__(
        self,
        retriever: ToolRetriever,
        session_manager: SessionStateManager,
        logger: RetrievalLogger,
        config: RetrievalConfig,
        tool_registry: dict[str, "ToolMapping"],
        ranker: Optional["RelevanceRanker"] = None,
        assembler: Optional["TieredAssembler"] = None,
        telemetry_scanner: Optional["TelemetryScanner"] = None,
        rolling_metrics: Optional[RollingMetrics] = None,
    ) -> None:
        self.retriever = retriever
        self.session_manager = session_manager
        self.logger = logger
        self.config = config
        self.tool_registry = tool_registry  # Reference, not copy
        self.ranker = ranker
        self.assembler = assembler
        self._telemetry_scanner = telemetry_scanner
        # Phase 9: optional RollingMetrics instance for rescore-rate tracking.
        # When set, rebuild_catalog() records each rebuild as a rescore event.
        self._rolling_metrics = rolling_metrics
        self._session_turns: dict[str, int] = {}  # session_id -> current turn number
        # Telemetry / roots state
        self._session_roots: dict[str, list[str]] = {}
        self._session_evidence: dict[str, WorkspaceEvidence] = {}
        # Conversation context stores
        self._session_tool_history: dict[str, list[str]] = {}
        self._session_arg_keys: dict[str, list[str]] = {}
        self._session_router_describes: dict[str, list[str]] = {}
        # CF-2: Separate router proxy accounting (on_tool_called is_router_proxy=True path only)
        self._session_router_proxies: dict[str, list[str]] = {}
        # Direct-call ledger: receives ONLY true direct calls (is_router_proxy=False).
        # Kept separate from _session_tool_history (conversation context) so that
        # RankingEvent.direct_tool_calls is semantically honest — no proxy calls contaminate it.
        self._direct_tool_calls: dict[str, list[str]] = {}
        # CF-3: Turn-scoped usage buckets for demotion protection
        self._current_turn_used: dict[str, set[str]] = {}
        self._just_finished_turn_used: dict[str, set[str]] = {}
        # CF-4: Per-session routing state (SessionRoutingState), turn guard, rebuild deferral, snapshot pin
        self._routing_states: dict[str, SessionRoutingState] = {}
        self._in_turn: dict[str, bool] = {}
        self._pending_rebuild: "dict[str, ToolMapping] | None" = None
        self._turn_snapshot_version: dict[str, str] = {}

    # ── Session roots / telemetry ─────────────────────────────────────────────

    async def set_session_roots(self, session_id: str, root_uris: list[str]) -> None:
        """Store root URIs for a session and immediately run telemetry scanner.

        Called by mcp_proxy.py after roots/list response or roots/list_changed.
        Caches WorkspaceEvidence so get_tools_for_list() can use it at turn zero.
        """
        self._session_roots[session_id] = root_uris
        if self.config.enable_telemetry and self._telemetry_scanner is not None:
            evidence = self._telemetry_scanner.scan_roots(root_uris)
            self._session_evidence[session_id] = evidence

    # ── Session context accessors ─────────────────────────────────────────────

    def get_session_tool_history(self, session_id: str) -> list[str]:
        """Return tool names called in this session, in call order."""
        return list(self._session_tool_history.get(session_id, []))

    def get_session_argument_keys(self, session_id: str) -> list[str]:
        """Return argument key names from recent tool calls, in call order."""
        return list(self._session_arg_keys.get(session_id, []))

    def get_session_router_describes(self, session_id: str) -> list[str]:
        """Return tool names the model asked to describe via routing tool, in call order."""
        return list(self._session_router_describes.get(session_id, []))

    # ── Index availability helpers ────────────────────────────────────────────

    def _index_available(self) -> bool:
        """True if the retriever has a built index (BMXFRetriever with snapshot)."""
        return (
            self.retriever is not None
            and hasattr(self.retriever, "_env_index")
            and self.retriever._env_index is not None  # type: ignore[union-attr]
        )

    def _keyword_retriever_available(self) -> bool:
        """True if a fallback keyword retriever is available."""
        # Check if there's a keyword retriever attached (via _keyword_retriever attr)
        kr = getattr(self, "_keyword_retriever", None)
        return kr is not None

    def _has_frequency_prior(self) -> bool:
        """True if FileRetrievalLogger JSONL log exists with recent events."""
        log_path = getattr(self.logger, "_path", None)
        if log_path is None:
            return False
        p = Path(log_path)
        if not p.exists():
            return False
        return True

    # ── Tier 4: Static category defaults ─────────────────────────────────────

    def _classify_project_type(
        self, evidence: Optional[WorkspaceEvidence]
    ) -> tuple[Optional[str], bool]:
        """Return (project_type, is_confident) based on telemetry tokens.

        Classification precedence (first match wins):
        1. infrastructure — terraform/kubernetes/helm signals
        2. rust_cli       — Cargo.toml or lang:rust
        3. python_web     — pyproject.toml or lang:python
        4. node_web       — package.json or lang:javascript/typescript
        5. generic        — workspace_confidence >= 0.45
        6. Not confident  — fall through to Tier 5
        """
        if evidence is None:
            return None, False

        tokens = evidence.merged_tokens
        token_keys = set(tokens.keys())

        infra_signals = {"infra:terraform", "infra:kubernetes", "manifest:Chart.yaml"}
        if token_keys & infra_signals:
            return "infrastructure", True

        if "manifest:Cargo.toml" in token_keys or "lang:rust" in token_keys:
            return "rust_cli", True

        if "manifest:pyproject.toml" in token_keys or "lang:python" in token_keys:
            return "python_web", True

        if (
            "manifest:package.json" in token_keys
            or "lang:javascript" in token_keys
            or "lang:typescript" in token_keys
        ):
            return "node_web", True

        if evidence.workspace_confidence >= 0.45:
            return "generic", True

        return None, False

    def _static_category_defaults(
        self, project_type: str, dynamic_k: int
    ) -> list[ScoredTool]:
        """Return ScoredTool list for the given project type using STATIC_CATEGORIES.

        always-tier tools score 1.0, likely-tier tools score 0.8.
        Result is capped at dynamic_k.
        """
        if not _HAS_STATIC_CATEGORIES or project_type not in STATIC_CATEGORIES:
            return []

        category = STATIC_CATEGORIES[project_type]
        always_ns = category.get("always", [])
        likely_ns = category.get("likely", [])

        result: list[ScoredTool] = []
        selected_keys: set[str] = set()

        # Select from always-tier namespaces
        for ns in always_ns:
            for key, mapping in self.tool_registry.items():
                if key not in selected_keys and mapping.server_name == ns:
                    result.append(ScoredTool(tool_key=key, tool_mapping=mapping, score=1.0))
                    selected_keys.add(key)
                    break  # one tool per namespace for static category

        # Select from likely-tier namespaces (if room)
        for ns in likely_ns:
            if len(result) >= dynamic_k:
                break
            for key, mapping in self.tool_registry.items():
                if key not in selected_keys and mapping.server_name == ns:
                    result.append(ScoredTool(tool_key=key, tool_mapping=mapping, score=0.8))
                    selected_keys.add(key)
                    break

        return result[:dynamic_k]

    # ── Tier 5: Time-decayed frequency prior ─────────────────────────────────

    def _frequency_prior_tools(self, dynamic_k: int) -> list[ScoredTool]:
        """Return time-decayed frequency prior from FileRetrievalLogger JSONL.

        Time window: last 7 days.
        Excludes shadow group events.
        Counts direct_tool_calls + router_proxies fields.
        Score = sum of exp(-0.1 * days_ago) per occurrence.
        """
        import math

        log_path = getattr(self.logger, "_path", None)
        if log_path is None:
            return []

        p = Path(log_path)
        if not p.exists():
            return []

        cutoff_epoch = time.time() - 7 * 24 * 3600
        scores: dict[str, float] = {}

        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Skip non-ranking events and shadow group
                    if event.get("type") == "alert":
                        continue
                    if event.get("group") == "shadow":
                        continue

                    # Use the timestamp field added to RankingEvent (Issue 3 fix).
                    # Legacy events without timestamp fall back to treating all entries
                    # as within the window (days_ago=0) — conservative but safe.
                    event_ts = event.get("timestamp")
                    if event_ts is not None:
                        event_ts = float(event_ts)
                        if event_ts < cutoff_epoch:
                            continue
                        days_ago = (time.time() - event_ts) / 86400.0
                    else:
                        days_ago = 0.0

                    decay = math.exp(-0.1 * days_ago)

                    for tool_name in event.get("direct_tool_calls", []):
                        scores[tool_name] = scores.get(tool_name, 0.0) + decay

                    for tool_name in event.get("router_proxies", []):
                        scores[tool_name] = scores.get(tool_name, 0.0) + decay

        except OSError:
            return []

        if not scores:
            return []

        # Sort by descending score
        sorted_tools = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        result: list[ScoredTool] = []
        for key, score in sorted_tools[:dynamic_k]:
            if key in self.tool_registry:
                result.append(
                    ScoredTool(
                        tool_key=key,
                        tool_mapping=self.tool_registry[key],
                        score=score,
                    )
                )

        return result[:dynamic_k]

    # ── Tier 6: Universal 12-tool fallback ───────────────────────────────────

    def _universal_fallback(self) -> list[ScoredTool]:
        """Return universal 12-tool set using TIER6_NAMESPACE_PRIORITY order.

        Algorithm:
        1. For each namespace in priority order, pick lexicographically smallest key
        2. If fewer than 12 selected, fill with lex-smallest remaining keys
        3. Expose at most 12 direct tools
        """
        selected: list[ScoredTool] = []
        selected_keys: set[str] = set()

        if _HAS_STATIC_CATEGORIES:
            priority = TIER6_NAMESPACE_PRIORITY
        else:
            priority = []

        # Build namespace → sorted keys map
        ns_keys: dict[str, list[str]] = {}
        for key, mapping in self.tool_registry.items():
            ns = mapping.server_name
            if ns not in ns_keys:
                ns_keys[ns] = []
            ns_keys[ns].append(key)
        for ns in ns_keys:
            ns_keys[ns].sort()

        # Step 1: namespace priority pass
        for ns in priority:
            if len(selected) >= 12:
                break
            if ns in ns_keys:
                for key in ns_keys[ns]:
                    if key not in selected_keys:
                        selected.append(
                            ScoredTool(
                                tool_key=key,
                                tool_mapping=self.tool_registry[key],
                                score=1.0,
                            )
                        )
                        selected_keys.add(key)
                        break

        # Step 2: fill remaining with lex-smallest unselected keys
        if len(selected) < 12:
            remaining = sorted(
                k for k in self.tool_registry if k not in selected_keys
            )
            for key in remaining:
                if len(selected) >= 12:
                    break
                selected.append(
                    ScoredTool(
                        tool_key=key,
                        tool_mapping=self.tool_registry[key],
                        score=0.5,
                    )
                )
                selected_keys.add(key)

        return selected[:12]

    # ── Main pipeline entry point ─────────────────────────────────────────────

    async def get_tools_for_list(
        self, session_id: str, conversation_context: str = ""
    ) -> list[types.Tool]:
        """Return tool list based on pipeline state, implementing the exact turn-boundary state machine.

        When disabled: returns ALL tools including cached/disconnected (client=None).
        When enabled: runs tiered fallback scoring pipeline and returns bounded active set.

        State machine executes in this exact order:
        1. Kill-switch check
        2. Turn-boundary entry: close previous turn
        3. Pending rebuild check
        4. Load/create SessionRoutingState
        5. Increment turn_number
        6. Pin catalog_version
        7. Score (6-tier fallback ladder)
        8. Promote evaluation
        9. Demote evaluation
        10. Post-boundary state sync
        11. Write scoring signals to state
        12. Build RankingEvent
        13. Set _in_turn = True
        14. Return tool list from state.active_tool_ids
        """
        # Step 1: Master kill switch — enabled=False always returns all tools
        if not self.config.enabled:
            return [m.tool for m in self.tool_registry.values()]

        t0 = time.monotonic()

        # Determine session group for canary routing
        group = get_session_group(session_id, self.config)

        # Shadow mode is the FIRST dispatch guard (Fix B — stabilization-pre-phase9-code-fixes).
        # When shadow_mode=True: score/log as normal (pipeline still runs), but always
        # return all tools (no active-set filtering). This check must precede any
        # rollout_stage == "ga" or "canary" filtering logic.
        if self.config.shadow_mode:
            is_filtered = False
        else:
            is_filtered = (
                self.config.rollout_stage == "ga"
                or (self.config.rollout_stage == "canary" and group == "canary")
            )

        # Step 2: Turn-boundary entry — close the previous turn
        if self._in_turn.get(session_id, False):
            # Close previous turn: roll current-turn bucket to just-finished snapshot
            self._just_finished_turn_used[session_id] = self._current_turn_used.pop(session_id, set())
            self._in_turn[session_id] = False
        else:
            # First call for this session: initialize just-finished bucket as empty
            self._just_finished_turn_used.setdefault(session_id, set())

        # Step 3: Execute pending rebuild if no session is mid-turn
        if self._pending_rebuild is not None and not any(self._in_turn.values()):
            rebuild = getattr(self.retriever, "rebuild_index", None)
            if callable(rebuild):
                rebuild(self._pending_rebuild)
            self._pending_rebuild = None

        # Step 4: Load or create SessionRoutingState; ensure SSM session exists
        state = self._routing_states.get(session_id)
        if state is None:
            state = SessionRoutingState(session_id=session_id)
            self._routing_states[session_id] = state
        self.session_manager.get_or_create_session(session_id)

        # Step 5: Increment turn_number (canonical per-session turn counter)
        state.turn_number += 1
        turn = state.turn_number
        self._session_turns[session_id] = turn

        # Step 6: Pin catalog_version to current snapshot
        current_version = ""
        if hasattr(self.retriever, "get_snapshot_version"):
            current_version = self.retriever.get_snapshot_version()
        self._turn_snapshot_version[session_id] = current_version
        state.catalog_version = current_version

        all_registry_keys = list(self.tool_registry.keys())
        candidates = list(self.tool_registry.values())

        # Dynamic K: base top_k, +3 if polyglot (>1 distinct lang: token), cap max_k
        dynamic_k = self.config.top_k
        evidence = self._session_evidence.get(session_id)
        if evidence:
            lang_tokens = [k for k in evidence.merged_tokens if k.startswith("lang:")]
            if len(lang_tokens) > 1:
                dynamic_k = self.config.max_k
        dynamic_k = min(self.config.max_k, dynamic_k)

        # direct_k always equals dynamic_k; routing tool is additive, not a K slot
        direct_k = dynamic_k

        workspace_evidence: Optional[WorkspaceEvidence] = evidence

        # ── Build query strings ──────────────────────────────────────────────

        env_query = ""
        if workspace_evidence and workspace_evidence.merged_tokens:
            env_query = " ".join(workspace_evidence.merged_tokens.keys())

        conv_query = ""
        if conversation_context:
            conv_query = _extract_conv_terms(conversation_context)

        ws_confidence = workspace_evidence.workspace_confidence if workspace_evidence else 0.0
        conv_confidence = min(1.0, len(conv_query.split()) / 10.0) if conv_query else 0.0
        roots_changed = False
        # Check for explicit tool name mention in raw conversation text.
        # Uses the raw conversation_context (not conv_query, which has underscores
        # replaced with spaces and is tokenized) so that tool_key fragments like
        # "search_repositories" can match.
        raw_conv_lower = (conversation_context or "").lower()
        explicit_tool_mention = any(
            k.split("__")[-1].lower() in raw_conv_lower
            for k in all_registry_keys
        ) if raw_conv_lower else False

        # Step 7: 6-tier fallback ladder
        fallback_tier = 1
        scored_tools: Optional[list[ScoredTool]] = None
        fusion_alpha: float = 0.0

        def _env_ctx() -> RetrievalContext:
            return RetrievalContext(session_id=session_id, query=env_query, query_mode="env")

        def _conv_ctx() -> RetrievalContext:
            return RetrievalContext(session_id=session_id, query=conv_query, query_mode="nl")

        # Tier 1: BMXF env + conv blend
        if (
            scored_tools is None
            and self._index_available()
            and env_query
            and conv_query
            and turn > 0
            and _HAS_FUSION
        ):
            try:
                env_ranked = await self.retriever.retrieve(_env_ctx(), candidates)
                conv_ranked = await self.retriever.retrieve(_conv_ctx(), candidates)
                fusion_alpha = _compute_alpha(
                    turn=turn,
                    workspace_confidence=ws_confidence,
                    conv_confidence=conv_confidence,
                    roots_changed=roots_changed,
                    explicit_tool_mention=explicit_tool_mention,
                )
                scored_tools = _weighted_rrf(env_ranked, conv_ranked, fusion_alpha)
                fallback_tier = 1
            except Exception as e:
                self.logger.warning("Tier 1 scoring failed: %s", e, exc_info=True)
                scored_tools = None
        # Tier 2: BMXF env-only
        if scored_tools is None and self._index_available() and env_query:
            try:
                scored_tools = await self.retriever.retrieve(_env_ctx(), candidates)
                fallback_tier = 2
            except Exception:
                scored_tools = None

        # Tier 3: KeywordRetriever env-only
        if scored_tools is None and self._keyword_retriever_available() and env_query:
            try:
                kr = getattr(self, "_keyword_retriever")
                scored_tools = await kr.retrieve(_env_ctx(), candidates)
                fallback_tier = 3
            except Exception:
                scored_tools = None

        # Tier 4: Static category defaults
        if scored_tools is None:
            project_type, project_type_confident = self._classify_project_type(workspace_evidence)
            if project_type_confident and project_type is not None and _HAS_STATIC_CATEGORIES:
                scored_tools = self._static_category_defaults(project_type, dynamic_k)
                if scored_tools:
                    fallback_tier = 4
                else:
                    scored_tools = None

        # Tier 5: Time-decayed frequency prior
        if scored_tools is None and self._has_frequency_prior():
            freq_tools = self._frequency_prior_tools(dynamic_k)
            if freq_tools:
                scored_tools = freq_tools
                fallback_tier = 5

        # Tier 6: Universal 12-tool set + routing tool
        if scored_tools is None:
            scored_tools = self._universal_fallback()
            fallback_tier = 6

        # Sort by descending score
        scored_tools.sort(key=lambda s: s.score, reverse=True)

        # Step 8: Promote evaluation at turn boundary
        current_active = self.session_manager.get_active_tools(session_id)
        active_key_set = set(current_active)

        k_minus_2 = max(1, dynamic_k - 2)
        promote_candidates: list[str] = []

        # Criterion 1: rank within K-2
        for scored_tool in scored_tools[:k_minus_2]:
            if scored_tool.tool_key not in active_key_set:
                promote_candidates.append(scored_tool.tool_key)

        # Criterion 2: router-proxied in >=2 of last 3 turns (CF-4)
        current_turn = state.turn_number
        for tool_key, turns in state.recent_router_proxies.items():
            recent = [t for t in turns if t >= current_turn - 3]
            if len(recent) >= 2 and tool_key not in active_key_set:
                if tool_key not in promote_candidates:
                    promote_candidates.append(tool_key)

        newly_promoted = self.session_manager.promote(session_id, promote_candidates)

        # Step 9: Demote evaluation at turn boundary
        # Update active set after promotion
        active_after_promote = self.session_manager.get_active_tools(session_id)
        score_by_key = {s.tool_key: s.score for s in scored_tools}
        rank_by_key = {s.tool_key: i for i, s in enumerate(scored_tools)}

        k_plus_3 = dynamic_k + 3
        demote_candidates: list[str] = []

        if turn > 1:  # Only evaluate demotion after turn 1
            for tool_key in list(active_after_promote):
                rank = rank_by_key.get(tool_key, len(scored_tools))
                if rank >= k_plus_3:
                    state.consecutive_low_rank[tool_key] = state.consecutive_low_rank.get(tool_key, 0) + 1
                    if state.consecutive_low_rank[tool_key] >= 2:
                        demote_candidates.append(tool_key)
                else:
                    state.consecutive_low_rank[tool_key] = 0

        # CF-3: Demotion protection from just-finished turn bucket (NOT full session history)
        just_finished = self._just_finished_turn_used.get(session_id, set())
        demoted = self.session_manager.demote(
            session_id, demote_candidates, just_finished, max_per_turn=3
        )
        for key in demoted:
            state.consecutive_low_rank.pop(key, None)

        # Step 10: Post-boundary state sync — single authoritative pass from SSM
        post_boundary_active = self.session_manager.get_active_tools(session_id)
        # Sort by current score before storing so any budget-limited slice is stable.
        state.active_tool_ids = sorted(
            post_boundary_active,
            key=lambda tool_key: (
                -score_by_key.get(tool_key, float("-inf")),
                tool_key,
            ),
        )
        state.router_enum_tool_ids = [k for k in all_registry_keys if k not in post_boundary_active]

        # Step 11: Write remaining scoring signals to state
        state.alpha = fusion_alpha
        state.active_k = len(state.active_tool_ids)
        state.fallback_tier = fallback_tier
        state.env_confidence = ws_confidence
        state.conv_confidence = conv_confidence

        latency_ms = (time.monotonic() - t0) * 1000.0

        # Step 12: Build RankingEvent with correct signal sources (CF-2)
        # Source direct_tool_calls from the direct-only ledger (_direct_tool_calls),
        # NOT from _session_tool_history (which includes proxy calls for conv context).
        session_direct_calls = list(self._direct_tool_calls.get(session_id, []))
        session_router_describes = list(self._session_router_describes.get(session_id, []))
        session_router_proxies = list(self._session_router_proxies.get(session_id, []))

        event = RankingEvent(
            session_id=session_id,
            turn_number=turn,
            catalog_version=current_version,
            workspace_hash=workspace_evidence.workspace_hash if workspace_evidence else None,
            workspace_confidence=ws_confidence,
            conv_confidence=conv_confidence,
            alpha=fusion_alpha,
            active_k=len(state.active_tool_ids),
            fallback_tier=fallback_tier,
            active_tool_ids=list(state.active_tool_ids),
            router_enum_size=len(state.router_enum_tool_ids),
            direct_tool_calls=session_direct_calls,
            router_describes=session_router_describes,
            router_proxies=session_router_proxies,
            scorer_latency_ms=latency_ms,
            group=group,
        )
        await self.logger.log_ranking_event(event)

        # Step 13: Mark session as mid-turn
        self._in_turn[session_id] = True

        # Step 14: Assemble return value from post-boundary active set (NOT raw scored slice)
        if is_filtered:
            # CANARY/GA: return bounded active set + routing tool
            active_tools_capped = state.active_tool_ids[:direct_k]
            active_tools_set = set(active_tools_capped)
            demoted_ids = state.router_enum_tool_ids

            routing_schema = None
            if (
                self.config.enable_routing_tool
                and demoted_ids
                and _HAS_ROUTING_TOOL
                and build_routing_tool_schema is not None
            ):
                routing_schema = build_routing_tool_schema(demoted_ids)

            if self.ranker is not None and self.assembler is not None:
                # Build ScoredTool list from active set for ranker/assembler
                active_scored = [s for s in scored_tools if s.tool_key in active_tools_set]
                ranked = self.ranker.rank(active_scored)
                result = self.assembler.assemble(
                    ranked, self.config, routing_tool_schema=routing_schema
                )
            else:
                result = [
                    self.tool_registry[k].tool
                    for k in active_tools_capped
                    if k in self.tool_registry
                ]
                if routing_schema is not None:
                    result.append(routing_schema)
        else:
            # SHADOW/CONTROL: return all tools (passthrough)
            result = [m.tool for m in self.tool_registry.values()]

        return result

    def rebuild_catalog(self, registry: "dict[str, ToolMapping]") -> None:
        """Rebuild the retriever's index when the tool registry changes.

        Called by MCPProxyServer.register_client() and unregister_client()
        after any registry mutation (WIRE-02). No-op if retriever does not
        implement rebuild_index (e.g. PassthroughRetriever).

        Mid-turn guard (CF-4): if any session is currently mid-turn, defer the
        rebuild to the next get_tools_for_list() call via _pending_rebuild.

        Phase 9: Records a rescore event on _rolling_metrics when configured,
        enabling rescore-rate alerting via AlertChecker.
        """
        if any(self._in_turn.values()):
            self._pending_rebuild = dict(registry)
            return
        rebuild = getattr(self.retriever, "rebuild_index", None)
        if callable(rebuild):
            rebuild(registry)
        # Phase 9: record this rebuild as a rescore event for rate monitoring
        if self._rolling_metrics is not None:
            self._rolling_metrics.record_rescore()

    async def on_tool_called(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict,
        is_router_proxy: bool = False,
    ) -> bool:
        """Called by _call_tool(). Records tool usage for conversation context and turn-boundary state.

        Never promotes, never mutates active set — those happen only at turn boundaries
        in get_tools_for_list(). Returns False always (list_changed emitted by _list_tools).

        When is_router_proxy=True, this is the single write path for router proxy accounting
        (CF-2): writes to _session_router_proxies and state.recent_router_proxies.
        """
        if not self.config.enabled:
            return False

        # CF-3: Write to turn-scoped bucket for demotion protection at next boundary
        self._current_turn_used.setdefault(session_id, set()).add(tool_name)
        # Write to session history for conv-context retrieval query construction only
        hist = self._session_tool_history.setdefault(session_id, [])
        hist.append(tool_name)
        # Record argument keys for conversation context
        arg_keys = self._session_arg_keys.setdefault(session_id, [])
        arg_keys.extend(arguments.keys())

        # Direct-call ledger: only true direct calls (not proxy calls) enter here.
        # This is the sole source for RankingEvent.direct_tool_calls so the field
        # is semantically honest — proxied tools do NOT contaminate it.
        if not is_router_proxy:
            self._direct_tool_calls.setdefault(session_id, []).append(tool_name)

        # CF-2: If this is a routing-tool proxy call, write all proxy accounting
        if is_router_proxy:
            # Flat session list for RankingEvent.router_proxies
            self._session_router_proxies.setdefault(session_id, []).append(tool_name)
            # Turn-indexed structure for 2-of-3-turn promotion criterion (CF-4)
            state = self._routing_states.get(session_id)
            if state is not None:
                turn = state.turn_number
                turns_list = state.recent_router_proxies.setdefault(tool_name, [])
                if not turns_list or turns_list[-1] != turn:
                    turns_list.append(turn)
                cutoff = turn - 3
                state.recent_router_proxies[tool_name] = [t for t in turns_list if t >= cutoff]

        return False

    def record_router_describe(self, session_id: str, tool_name: str) -> None:
        """Record a tool name that was described via the routing tool.

        Called by mcp_proxy._call_tool() after a describe=True routing call succeeds.
        Used by get_session_router_describes() to provide conversation context.

        Issue 2 fix: router describe targets are now written to session state.
        """
        describes = self._session_router_describes.setdefault(session_id, [])
        describes.append(tool_name)

    def cleanup_session(self, session_id: str) -> None:
        """Release all per-session state held by this pipeline instance.

        Pops session_id from every per-session dict and delegates to
        SessionStateManager.cleanup_session(). Safe to call for sessions
        that were never created (all pops are no-ops).
        """
        self._session_turns.pop(session_id, None)
        self._session_roots.pop(session_id, None)
        self._session_evidence.pop(session_id, None)
        self._session_tool_history.pop(session_id, None)
        self._session_arg_keys.pop(session_id, None)
        self._session_router_describes.pop(session_id, None)
        self._session_router_proxies.pop(session_id, None)
        self._direct_tool_calls.pop(session_id, None)
        self._current_turn_used.pop(session_id, None)
        self._just_finished_turn_used.pop(session_id, None)
        self._routing_states.pop(session_id, None)
        self._in_turn.pop(session_id, None)
        self._turn_snapshot_version.pop(session_id, None)
        self.session_manager.cleanup_session(session_id)
