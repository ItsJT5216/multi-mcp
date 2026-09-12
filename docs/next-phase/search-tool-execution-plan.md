# Search Tool Dynamic Routing — Execution Plan

**Goal:** Replace the bounded active-set + routing-tool architecture with a single `search_tools` tool exposed via `tools/list`. The model discovers and invokes tools on demand through search, describe, and execute modes. Cache-stable, bounded exposure, works with every MCP client.

**Total Waves:** 4
**Total Tasks:** 10
**Max Parallel Tasks in Single Wave:** 5

---

## Codebase Reconnaissance

```text
CODEBASE RECONNAISSANCE:
├── Project Structure: Python package at src/multimcp/, retrieval subsystem at src/multimcp/retrieval/
├── Tech Stack: Python 3.10+, MCP SDK (mcp>=1.26.0), Pydantic 2+, pytest + pytest-asyncio (auto mode), loguru, anyio
├── Key Conventions:
│   ├── Tool keys use "server__tool" format (double-underscore separator)
│   ├── ToolMapping dataclass: (server_name: str, client: Optional[ClientSession], tool: types.Tool)
│   ├── Retrieval uses ToolRetriever ABC → list[ScoredTool] results
│   ├── Pipeline manages session state via internal dicts keyed by session_id
│   ├── Tests use unittest.mock.MagicMock for ToolMapping objects
│   └── asyncio_mode = "auto" in pyproject.toml — no @pytest.mark.asyncio needed on test funcs in newer pytest-asyncio
├── Reusable Infrastructure:
│   ├── BMXFRetriever with dual index (env/nl), field-weighted scoring — NO CHANGES
│   ├── BMXIndex with self-tuning alpha/beta — NO CHANGES
│   ├── TelemetryScanner for workspace evidence — NO CHANGES
│   ├── weighted_rrf() and compute_alpha() in fusion.py
│   ├── build_snapshot() in catalog.py — NO CHANGES
│   ├── FileRetrievalLogger + NullLogger — NO CHANGES
│   └── RetrievalConfig, ScoredTool, RankingEvent, ToolDoc, WorkspaceEvidence dataclasses
└── Import Conventions: "from src.multimcp.xxx" for cross-package, relative imports within retrieval/
```

---

## File Inventory

```text
FILE INVENTORY:
├── Files to MODIFY:
│   ├── src/multimcp/retrieval/models.py         — Task 1.1: Add SearchSessionState, SearchEvent dataclasses
│   ├── src/multimcp/retrieval/session.py         — Task 1.2: Rewrite to usage-tracking SessionStateManager
│   ├── src/multimcp/retrieval/routing_tool.py    — Task 1.3: Rewrite with SEARCH_TOOL_SCHEMA + handle_search_tool_call
│   ├── src/multimcp/retrieval/assembler.py       — Task 1.4: Add format_search_results() markdown table method
│   ├── src/multimcp/retrieval/fusion.py          — Task 1.5: Simplify compute_alpha (no alpha-decay)
│   ├── src/multimcp/retrieval/pipeline.py        — Task 2.1: Add search/describe/execute, gut get_tools_for_list
│   ├── src/multimcp/mcp_proxy.py                 — Task 3.1: Wire search_tools into _list_tools and _call_tool
│   └── src/multimcp/multi_mcp.py                 — Task 4.1: Simplified pipeline wiring
├── Files to CREATE:
│   ├── tests/test_search_tool_modes.py           — Task 2.2: Tests for routing_tool + assembler search formatting
│   └── tests/test_search_pipeline.py             — Task 3.2: Tests for pipeline.search/describe/execute
├── Files to READ (no modifications):
│   ├── src/multimcp/retrieval/bmx_retriever.py   — Retriever interface, retrieve() signature
│   ├── src/multimcp/retrieval/bmx_index.py       — BMXIndex.search_fields() interface
│   ├── src/multimcp/retrieval/base.py            — ToolRetriever ABC, PassthroughRetriever
│   ├── src/multimcp/retrieval/catalog.py         — build_snapshot(), ToolDoc
│   ├── src/multimcp/retrieval/logging.py         — RetrievalLogger ABC, NullLogger, FileRetrievalLogger
│   ├── src/multimcp/retrieval/keyword.py         — KeywordRetriever (Tier 3 fallback)
│   ├── src/multimcp/retrieval/static_categories.py — Tier 4 static defaults
│   ├── src/multimcp/retrieval/rollout.py         — get_session_group()
│   ├── src/multimcp/retrieval/metrics.py         — RollingMetrics
│   ├── src/multimcp/retrieval/telemetry/         — TelemetryScanner, evidence types
│   ├── src/multimcp/yaml_config.py               — MultiMCPConfig, RetrievalSettings
│   ├── pyproject.toml                            — pytest config, dependencies
│   └── conftest.py                               — Root pytest config
```

---

## Task Decomposition

```text
TASK DECOMPOSITION:
├── Task 1.1: Update models.py
│   ├── Modifies: src/multimcp/retrieval/models.py
│   ├── Reads: nothing
│   ├── Depends on: nothing
│   └── Produces: SearchSessionState dataclass, SearchEvent logging dataclass, DynamicKResult helper
│
├── Task 1.2: Rewrite session.py
│   ├── Modifies: src/multimcp/retrieval/session.py
│   ├── Reads: src/multimcp/retrieval/models.py (RetrievalConfig — existing, unchanged)
│   ├── Depends on: nothing (new SessionState defined inline, does NOT import from models.py)
│   └── Produces: New SessionStateManager with record_tool_use/record_describe/record_search/get_recently_used/get_suggestion_candidates
│
├── Task 1.3: Rewrite routing_tool.py
│   ├── Modifies: src/multimcp/retrieval/routing_tool.py
│   ├── Reads: nothing (pipeline is a runtime argument)
│   ├── Depends on: nothing
│   └── Produces: SEARCH_TOOL_SCHEMA constant, SEARCH_TOOL_NAME constant, handle_search_tool_call() async function
│
├── Task 1.4: Update assembler.py
│   ├── Modifies: src/multimcp/retrieval/assembler.py
│   ├── Reads: src/multimcp/retrieval/models.py (ScoredTool — existing, unchanged)
│   ├── Depends on: nothing
│   └── Produces: format_search_results() method on TieredAssembler, returning markdown table string
│
├── Task 1.5: Simplify fusion.py
│   ├── Modifies: src/multimcp/retrieval/fusion.py
│   ├── Reads: src/multimcp/retrieval/models.py (ScoredTool — existing, unchanged)
│   ├── Depends on: nothing
│   └── Produces: Simplified compute_alpha with query-adaptive alpha (no turn-based decay)
│
├── Task 2.1: Update pipeline.py — search/describe/execute
│   ├── Modifies: src/multimcp/retrieval/pipeline.py
│   ├── Reads: all retrieval modules
│   ├── Depends on: Task 1.1 (models), Task 1.2 (session), Task 1.4 (assembler)
│   └── Produces: pipeline.search(), pipeline.describe(), pipeline.execute(), simplified get_tools_for_list()
│
├── Task 2.2: Create tests/test_search_tool_modes.py
│   ├── Creates: tests/test_search_tool_modes.py
│   ├── Reads: routing_tool.py, assembler.py, models.py
│   ├── Depends on: Task 1.3 (routing_tool), Task 1.4 (assembler)
│   └── Produces: Test coverage for search tool schema, three modes, markdown formatting
│
├── Task 3.1: Update mcp_proxy.py
│   ├── Modifies: src/multimcp/mcp_proxy.py
│   ├── Reads: routing_tool.py, pipeline.py
│   ├── Depends on: Task 1.3 (routing_tool), Task 2.1 (pipeline)
│   └── Produces: _list_tools returns only SEARCH_TOOL_SCHEMA, _call_tool routes search_tools
│
├── Task 3.2: Create tests/test_search_pipeline.py
│   ├── Creates: tests/test_search_pipeline.py
│   ├── Reads: pipeline.py, session.py, assembler.py, models.py
│   ├── Depends on: Task 2.1 (pipeline)
│   └── Produces: Test coverage for pipeline.search/describe/execute, session tracking, fallback tiers
│
├── Task 4.1: Update multi_mcp.py
│   ├── Modifies: src/multimcp/multi_mcp.py
│   ├── Reads: pipeline.py, mcp_proxy.py, yaml_config.py
│   ├── Depends on: Task 2.1 (pipeline), Task 3.1 (mcp_proxy)
│   └── Produces: Simplified pipeline wiring in run(), removal of routing tool config paths
```

---

## Dependency Proof Table

| Task | Claims to depend on | Proof: Cannot produce correct output because... | Verdict |
|------|--------------------|-------------------------------------------------|---------|
| 1.2 → 1.1 | models.py changes | session.py imports only RetrievalConfig from models.py, which is unchanged. New SessionState is defined inline in session.py. The subagent does not need models.py changes on disk. | **FALSE** |
| 1.3 → 2.1 | pipeline.py changes | routing_tool.py takes `pipeline` as a runtime function argument and calls `pipeline.search()`, `pipeline.describe()`, `pipeline.execute()`. The subagent needs to know the method signatures (specified in plan), NOT the file on disk. The handler is a standalone function. | **FALSE** |
| 1.4 → 1.1 | models.py changes | assembler.py imports ScoredTool from models.py — this type already exists and is not changing. The new `format_search_results()` method uses ScoredTool fields (tool_key, tool_mapping.tool.description, tool_mapping.tool.inputSchema). No new types needed. | **FALSE** |
| 2.1 → 1.1 | models.py changes | pipeline.py will `from .models import SearchSessionState, SearchEvent` — these types don't exist until Task 1.1 adds them. Import fails without them on disk. | **REAL** |
| 2.1 → 1.2 | session.py changes | pipeline.py calls `self.session_manager.record_tool_use()`, `.record_describe()`, `.record_search()`, `.get_recently_used()`, `.get_suggestion_candidates()` — none of these methods exist until Task 1.2 rewrites session.py. Runtime calls would AttributeError. | **REAL** |
| 2.1 → 1.4 | assembler.py changes | pipeline.py calls `self.assembler.format_search_results()` — this method does not exist until Task 1.4 adds it. Runtime call would AttributeError. | **REAL** |
| 2.1 → 1.5 | fusion.py changes | pipeline.py calls `compute_alpha()` and `weighted_rrf()` — both already exist in fusion.py. Task 1.5 only simplifies compute_alpha's internal logic, not its signature. | **FALSE** |
| 2.2 → 1.3 | routing_tool.py changes | Test file imports SEARCH_TOOL_SCHEMA and handle_search_tool_call from routing_tool.py. These don't exist until Task 1.3 writes them. | **REAL** |
| 2.2 → 1.4 | assembler.py changes | Test file imports format_search_results from assembler. Doesn't exist until Task 1.4. | **REAL** |
| 3.1 → 1.3 | routing_tool.py changes | mcp_proxy.py will import SEARCH_TOOL_NAME and SEARCH_TOOL_SCHEMA from routing_tool.py. These constants don't exist until Task 1.3. | **REAL** |
| 3.1 → 2.1 | pipeline.py changes | mcp_proxy.py calls `pipeline.search()`, `pipeline.describe()`, `pipeline.execute()`. These methods don't exist until Task 2.1. | **REAL** |
| 3.2 → 2.1 | pipeline.py changes | Test file tests pipeline.search/describe/execute methods. Don't exist until Task 2.1. | **REAL** |
| 4.1 → 2.1 | pipeline.py changes | multi_mcp.py constructs pipeline with potentially new constructor signature. | **REAL** |
| 4.1 → 3.1 | mcp_proxy.py changes | multi_mcp.py relies on updated proxy behavior (tools/list returning search tool). Must be consistent. | **REAL** |

---

## Conflict Analysis

```text
CONFLICT ANALYSIS:
├── File Conflicts: NONE within any wave
│   └── Every file is modified by exactly one task
├── Dependency Conflicts (resolved by wave ordering):
│   ├── Task 2.1 needs Tasks 1.1, 1.2, 1.4 → Wave 2 after Wave 1
│   ├── Task 2.2 needs Tasks 1.3, 1.4 → Wave 2 after Wave 1
│   ├── Task 3.1 needs Tasks 1.3 + 2.1 → Wave 3 after Wave 2
│   ├── Task 3.2 needs Task 2.1 → Wave 3 after Wave 2
│   └── Task 4.1 needs Tasks 2.1 + 3.1 → Wave 4 after Wave 3
├── FALSE Dependencies Exposed:
│   ├── Task 1.2 does NOT depend on 1.1 — SessionState defined inline
│   ├── Task 1.3 does NOT depend on 2.1 — pipeline is a runtime arg
│   ├── Task 1.4 does NOT depend on 1.1 — uses existing ScoredTool only
│   └── Task 2.1 does NOT depend on 1.5 — existing fusion signatures are stable
└── No Conflicts:
    └── All Wave 1 tasks touch different files → fully parallel
```

---

## Wave Assignment

```text
WAVE ASSIGNMENT:
├── Wave 1 (5 tasks in parallel):
│   ├── Task 1.1: Update models.py          — modifies src/multimcp/retrieval/models.py
│   ├── Task 1.2: Rewrite session.py        — modifies src/multimcp/retrieval/session.py
│   ├── Task 1.3: Rewrite routing_tool.py   — modifies src/multimcp/retrieval/routing_tool.py
│   ├── Task 1.4: Update assembler.py       — modifies src/multimcp/retrieval/assembler.py
│   └── Task 1.5: Simplify fusion.py        — modifies src/multimcp/retrieval/fusion.py
│   VALIDATION: No file conflicts ✓ No intra-dependencies ✓ Current state ✓
│
├── Wave 2 (2 tasks in parallel):
│   ├── Task 2.1: Update pipeline.py        — modifies src/multimcp/retrieval/pipeline.py
│   └── Task 2.2: Create test_search_tool_modes.py — creates tests/test_search_tool_modes.py
│   VALIDATION: No file conflicts ✓ No intra-dependencies ✓ W1 deps satisfied ✓
│
├── Wave 3 (2 tasks in parallel):
│   ├── Task 3.1: Update mcp_proxy.py       — modifies src/multimcp/mcp_proxy.py
│   └── Task 3.2: Create test_search_pipeline.py — creates tests/test_search_pipeline.py
│   VALIDATION: No file conflicts ✓ No intra-dependencies ✓ W2 deps satisfied ✓
│
└── Wave 4 (1 task):
    └── Task 4.1: Update multi_mcp.py       — modifies src/multimcp/multi_mcp.py
    VALIDATION: No file conflicts ✓ W3 deps satisfied ✓
```

---

## Parallelism Stress Test

1. **Wave 2 tasks:** Can Task 2.1 move to Wave 1? No — it imports SearchSessionState from models.py (Task 1.1), calls session_manager.record_tool_use() (Task 1.2), calls assembler.format_search_results() (Task 1.4). Can Task 2.2 move to Wave 1? No — it imports SEARCH_TOOL_SCHEMA (Task 1.3) and format_search_results (Task 1.4).

2. **Wave 3 tasks:** Can Task 3.1 move to Wave 2? No — calls pipeline.search/describe/execute (Task 2.1). Can Task 3.2 move to Wave 2? No — tests pipeline.search/describe/execute (Task 2.1).

3. **Wave 4:** Can Task 4.1 move to Wave 3? No — depends on updated mcp_proxy.py (Task 3.1).

4. **Wave count vs task count:** 4 waves for 10 tasks. Ratio = 0.4. Wave 1 has 5 parallel tasks. Parallelism is well-optimized.

---

## Wave 1: Foundation Modules

> **PARALLEL EXECUTION:** All 5 tasks in this wave run simultaneously.
>
> **Dependencies:** None — all tasks execute against current repo state.
> **File Safety:**
> - `models.py`: only Task 1.1 ✓
> - `session.py`: only Task 1.2 ✓
> - `routing_tool.py`: only Task 1.3 ✓
> - `assembler.py`: only Task 1.4 ✓
> - `fusion.py`: only Task 1.5 ✓

---

### Task 1.1: Update models.py — Add Search Architecture Types

**Files:**
- Modify: `src/multimcp/retrieval/models.py`

**Codebase References:**
- Existing dataclasses to preserve: `src/multimcp/retrieval/models.py` — ALL existing classes (RetrievalContext, ScoredTool, RetrievalConfig, ToolDoc, ToolCatalogSnapshot, RootEvidence, WorkspaceEvidence, SessionRoutingState, RankingEvent) MUST remain exactly as-is for backward compatibility. Tests import them.
- Import pattern: `from __future__ import annotations` at top, `from dataclasses import dataclass, field`, `from typing import ...`
- Existing ScoredTool: `src/multimcp/retrieval/models.py:L16-L21` — used throughout pipeline, assembler, fusion

**Implementation Details:**

Add the following three new dataclasses AFTER the existing `RankingEvent` class (append to end of file, do NOT modify any existing class):

1. **`SearchSessionState`** — Per-session state for the search-tool architecture. This replaces the conceptual role of `SessionRoutingState` for new code but does NOT delete `SessionRoutingState` (existing tests reference it).

```python
@dataclass
class SearchSessionState:
    """Per-session mutable state for the search-tool architecture.

    Tracks tool discovery and usage patterns within a single MCP session.
    Fed into search result ranking (recently used tools get boosted,
    frequently described tools surface in suggestions).
    """
    session_id: str
    tools_used: list[str] = field(default_factory=list)           # ordered by recency
    tools_described: list[str] = field(default_factory=list)      # tools model has seen schemas for
    search_queries: list[str] = field(default_factory=list)       # intent history
    tool_call_counts: dict[str, int] = field(default_factory=dict)  # frequency per tool
    last_search_results: list[str] = field(default_factory=list)  # tool keys from most recent search
```

2. **`SearchEvent`** — Structured log entry for search-mode pipeline operations.

```python
@dataclass
class SearchEvent:
    """Structured log entry for a search-tool pipeline operation.

    Emitted per search/describe/execute call. Extends the observability
    model alongside RankingEvent.
    """
    session_id: str
    event_type: str                 # "search" | "describe" | "execute"
    intent: str = ""                # search query (search mode only)
    tool_key: str = ""              # target tool (describe/execute modes)
    result_count: int = 0           # number of tools returned (search mode)
    fallback_tier: int = 0          # which tier produced results
    alpha: float = 0.0              # RRF alpha used
    latency_ms: float = 0.0
    validation_error: str = ""      # schema validation error (execute mode)
    timestamp: float = field(default_factory=time.time)
```

3. **`DynamicKResult`** — Return type for dynamic K calculation.

```python
@dataclass
class DynamicKResult:
    """Result of dynamic K calculation for search result count.

    Encapsulates the number of results to return and the confidence
    classification that determined it.
    """
    k: int                          # number of results to return (3-5)
    confidence: str                 # "high" | "medium" | "low"
    max_score: float = 0.0          # highest BMXF score in results
    suggestion: str = ""            # query refinement hint (low confidence only)
```

Also add to the `__all__`-equivalent exports: update `src/multimcp/retrieval/__init__.py` is NOT this task's responsibility — that is handled in a later integration step. The new types just need to exist in models.py.

**Acceptance Criteria:**
- [ ] All three new dataclasses (`SearchSessionState`, `SearchEvent`, `DynamicKResult`) are defined in models.py
- [ ] Every existing dataclass in models.py is UNCHANGED (byte-for-byte identical)
- [ ] `from src.multimcp.retrieval.models import SearchSessionState, SearchEvent, DynamicKResult` succeeds
- [ ] All existing model imports still work: `from src.multimcp.retrieval.models import RetrievalConfig, ScoredTool, RankingEvent, SessionRoutingState, WorkspaceEvidence, ToolDoc, ToolCatalogSnapshot, RootEvidence, RetrievalContext`

**What Complete Looks Like:**
models.py contains all original classes unchanged, plus three new dataclasses appended at the end. All imports resolve.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.retrieval.models import SearchSessionState, SearchEvent, DynamicKResult, RetrievalConfig, ScoredTool, RankingEvent; print('OK')"`
- Expected: `OK`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_retrieval_models.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: All existing model tests pass (exit 0)

---

### Task 1.2: Rewrite session.py — Usage-Tracking SessionStateManager

**Files:**
- Modify: `src/multimcp/retrieval/session.py`

**Codebase References:**
- Current session.py: `src/multimcp/retrieval/session.py` — has `SessionStateManager` with `promote()`, `demote()`, `get_active_tools()`, `add_tools()`, `get_or_create_session()`, `cleanup_session()`
- Current import from models: `from .models import RetrievalConfig` (L3)
- Pipeline calls to session_manager (current): `src/multimcp/retrieval/pipeline.py:L192` — `self.session_manager.get_or_create_session(session_id)`, `self.session_manager.promote()`, `self.session_manager.demote()`, `self.session_manager.get_active_tools()`, `self.session_manager.cleanup_session()`
- Test file: `tests/test_retrieval_session.py` — existing tests for promote/demote

**Implementation Details:**

**CRITICAL:** The existing `SessionStateManager` class and ALL its methods (`get_or_create_session`, `get_active_tools`, `add_tools`, `promote`, `demote`, `cleanup_session`) MUST be preserved exactly as-is. The existing pipeline.py `get_tools_for_list()` still calls these methods and must continue to work until Wave 2 modifies pipeline.py. Existing tests in `tests/test_retrieval_session.py` and `tests/test_session_promote_demote.py` verify this behavior.

**Add** the following NEW class and NEW methods to the EXISTING file, placed AFTER the existing `SessionStateManager` class:

```python
@dataclass
class SessionState:
    """Per-session usage tracking state for search-tool architecture.

    Defined here (not in models.py) because it is tightly coupled to
    SearchSessionStateManager's mutation logic.
    """
    session_id: str
    tools_used: list[str] = field(default_factory=list)
    tools_described: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    last_search_results: list[str] = field(default_factory=list)
```

```python
class SearchSessionStateManager:
    """Manages per-session usage tracking for the search-tool architecture.

    Unlike SessionStateManager (which manages active/demoted tool sets),
    this class tracks discovery patterns: what was searched, described,
    and executed. This data feeds search result ranking.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        """Return existing session or create a new empty one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]

    def record_tool_use(self, session_id: str, tool_key: str) -> None:
        """Track that a tool was executed. Updates recency list and frequency counter."""
        state = self.get_or_create(session_id)
        # Remove from current position (if present) and append to end (most recent)
        if tool_key in state.tools_used:
            state.tools_used.remove(tool_key)
        state.tools_used.append(tool_key)
        state.tool_call_counts[tool_key] = state.tool_call_counts.get(tool_key, 0) + 1

    def record_describe(self, session_id: str, tool_key: str) -> None:
        """Track that a tool schema was viewed."""
        state = self.get_or_create(session_id)
        if tool_key not in state.tools_described:
            state.tools_described.append(tool_key)

    def record_search(self, session_id: str, intent: str, results: list[str]) -> None:
        """Track search query and result tool keys."""
        state = self.get_or_create(session_id)
        state.search_queries.append(intent)
        state.last_search_results = list(results)

    def get_recently_used(self, session_id: str, n: int = 3) -> list[str]:
        """Return last n tools used, most recent first."""
        state = self._sessions.get(session_id)
        if state is None:
            return []
        return list(reversed(state.tools_used[-n:]))

    def get_suggestion_candidates(self, session_id: str, scored_keys: list[str]) -> list[str]:
        """Return top-scored tool keys not yet used or in last search results.

        Args:
            session_id: The session ID.
            scored_keys: Tool keys ordered by descending BMXF score.

        Returns:
            Up to 2 tool keys from scored_keys that are NOT in tools_used
            and NOT in last_search_results for this session.
        """
        state = self._sessions.get(session_id)
        if state is None:
            return scored_keys[:2]
        used = set(state.tools_used)
        last_results = set(state.last_search_results)
        exclude = used | last_results
        candidates = [k for k in scored_keys if k not in exclude]
        return candidates[:2]

    def cleanup_session(self, session_id: str) -> None:
        """Release session state."""
        self._sessions.pop(session_id, None)
```

Add these imports at the top of the file (after the existing `from __future__ import annotations`):

```python
from dataclasses import dataclass, field
```

**Acceptance Criteria:**
- [ ] Existing `SessionStateManager` class is UNCHANGED — all original methods preserved
- [ ] New `SessionState` dataclass is defined with all 6 fields
- [ ] New `SearchSessionStateManager` class has all 7 methods: `get_or_create`, `record_tool_use`, `record_describe`, `record_search`, `get_recently_used`, `get_suggestion_candidates`, `cleanup_session`
- [ ] `record_tool_use` maintains recency ordering (last call = last element) and increments frequency
- [ ] `get_recently_used(n=3)` returns most-recent-first ordering
- [ ] `get_suggestion_candidates` excludes used tools and last search results
- [ ] Existing test `tests/test_retrieval_session.py` still passes
- [ ] Existing test `tests/test_session_promote_demote.py` still passes

**What Complete Looks Like:**
session.py contains the original `SessionStateManager` class unchanged, plus the new `SessionState` dataclass and `SearchSessionStateManager` class below it. Both old and new APIs work.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.retrieval.session import SessionStateManager, SearchSessionStateManager, SessionState; print('OK')"`
- Expected: `OK`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_retrieval_session.py tests/test_session_promote_demote.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: All existing tests pass (exit 0)

---

### Task 1.3: Rewrite routing_tool.py — Static Search Tool Schema + Three-Mode Handler

**Files:**
- Modify: `src/multimcp/retrieval/routing_tool.py`

**Codebase References:**
- Current routing_tool.py: `src/multimcp/retrieval/routing_tool.py` — has `ROUTING_TOOL_NAME`, `ROUTING_TOOL_KEY`, `build_routing_tool_schema()`, `format_namespace_grouped()`, `handle_routing_call()`
- Current imports in mcp_proxy.py: `src/multimcp/mcp_proxy.py:L259-L262` — `from src.multimcp.retrieval.routing_tool import ROUTING_TOOL_NAME, handle_routing_call`
- Test file: `tests/test_routing_tool.py` — tests for build_routing_tool_schema, handle_routing_call, format_namespace_grouped
- Spec schema definition: spec sheet lines 35-61

**Implementation Details:**

**CRITICAL:** The existing constants `ROUTING_TOOL_NAME`, `ROUTING_TOOL_KEY`, `build_routing_tool_schema()`, `format_namespace_grouped()`, and `handle_routing_call()` are imported by `mcp_proxy.py` and tested in `tests/test_routing_tool.py`. These MUST remain in the file for backward compatibility until `mcp_proxy.py` is updated in Wave 3. Do NOT delete them. Add new code alongside them.

Add the following to the file (after existing code):

```python
from mcp import types

# ── Search Tool Architecture ─────────────────────────────────────────────────

SEARCH_TOOL_NAME = "search_tools"

SEARCH_TOOL_SCHEMA = types.Tool(
    name=SEARCH_TOOL_NAME,
    description=(
        "Search for and execute tools from the aggregated tool catalog. "
        "Returns relevant tools based on your intent and workspace context. "
        "Call with intent to discover tools, with name and describe=true to "
        "get full schema, or with name and arguments to execute directly."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "Natural language description of what you need",
            },
            "name": {
                "type": "string",
                "description": "Specific tool key in server__tool format",
            },
            "describe": {
                "type": "boolean",
                "description": "Return full JSON schema instead of executing",
                "default": False,
            },
            "arguments": {
                "type": "object",
                "description": "Arguments to pass when executing a tool",
                "default": {},
            },
        },
    },
)


async def handle_search_tool_call(
    pipeline: object,
    session_id: str,
    arguments: dict,
) -> str:
    """Route a search_tools call to the appropriate pipeline method.

    Three modes:
    1. Search: intent provided, no name → pipeline.search()
    2. Describe: name + describe=true (or name alone with no args) → pipeline.describe()
    3. Execute: name + arguments → pipeline.execute()

    Args:
        pipeline: RetrievalPipeline instance (typed as object to avoid circular import).
        session_id: Current MCP session identifier.
        arguments: The arguments dict from the tool call.

    Returns:
        String response (markdown table, JSON schema, tool result, or error message).
    """
    intent = arguments.get("intent")
    name = arguments.get("name")
    describe = arguments.get("describe", False)
    tool_arguments = arguments.get("arguments", {})

    if intent and not name:
        # Search mode
        return await pipeline.search(session_id, intent)

    if name and describe:
        # Describe mode
        return await pipeline.describe(session_id, name)

    if name and tool_arguments:
        # Execute mode
        return await pipeline.execute(session_id, name, tool_arguments)

    if name and not describe and not tool_arguments:
        # Bare name, no args — treat as describe
        return await pipeline.describe(session_id, name)

    return "Provide either 'intent' to search or 'name' to describe/execute a tool."
```

**Acceptance Criteria:**
- [ ] `SEARCH_TOOL_NAME` equals `"search_tools"`
- [ ] `SEARCH_TOOL_SCHEMA` is a `types.Tool` with the exact inputSchema from the spec (4 properties: intent, name, describe, arguments)
- [ ] `SEARCH_TOOL_SCHEMA.inputSchema` has NO `required` field (all properties optional — mode is inferred from which are present)
- [ ] `SEARCH_TOOL_SCHEMA.inputSchema` is a static dict literal — NOT dynamically generated (critical for MCP client caching)
- [ ] `handle_search_tool_call()` is async and returns a string
- [ ] Mode routing: `intent` without `name` → calls `pipeline.search(session_id, intent)`
- [ ] Mode routing: `name` + `describe=True` → calls `pipeline.describe(session_id, name)`
- [ ] Mode routing: `name` + `arguments` (non-empty) → calls `pipeline.execute(session_id, name, tool_arguments)`
- [ ] Mode routing: `name` alone (no describe, no arguments) → calls `pipeline.describe(session_id, name)` (bare name = describe)
- [ ] Mode routing: no intent, no name → returns error string
- [ ] Old constants/functions (`ROUTING_TOOL_NAME`, `ROUTING_TOOL_KEY`, `build_routing_tool_schema`, `format_namespace_grouped`, `handle_routing_call`) still exist and are importable
- [ ] Existing tests in `tests/test_routing_tool.py` still pass

**What Complete Looks Like:**
routing_tool.py contains all original code unchanged plus the new `SEARCH_TOOL_NAME`, `SEARCH_TOOL_SCHEMA`, and `handle_search_tool_call()` at the bottom.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.retrieval.routing_tool import SEARCH_TOOL_NAME, SEARCH_TOOL_SCHEMA, handle_search_tool_call, ROUTING_TOOL_NAME, build_routing_tool_schema; print(SEARCH_TOOL_NAME, SEARCH_TOOL_SCHEMA.name)"`
- Expected: `search_tools search_tools`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_routing_tool.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: All existing tests pass (exit 0)

---

### Task 1.4: Update assembler.py — Add Markdown Table Formatter

**Files:**
- Modify: `src/multimcp/retrieval/assembler.py`

**Codebase References:**
- Current assembler.py: `src/multimcp/retrieval/assembler.py` — has `TieredAssembler` with `assemble()` method
- ScoredTool definition: `src/multimcp/retrieval/models.py:L16-L21` — `tool_key: str`, `tool_mapping: ToolMapping`, `score: float`, `tier: str`
- ToolMapping definition: `src/multimcp/mcp_proxy.py:L49-L52` — `server_name: str`, `client: Optional[ClientSession]`, `tool: types.Tool`
- types.Tool.inputSchema: dict with `properties` key containing parameter definitions
- Spec response format: spec sheet lines 86-109

**Implementation Details:**

Add the following method to the `TieredAssembler` class, and add a standalone helper function:

```python
def _abbreviate_param_type(self, prop: dict) -> str:
    """Convert JSON Schema property to abbreviated type hint.

    Examples:
        {"type": "string"} → "str"
        {"type": "integer"} → "int"
        {"type": "boolean"} → "bool"
        {"type": "object"} → "obj"
        {"type": "array", "items": {"type": "string"}} → "list[str]"
        {"type": "string", "enum": [...]} → "enum"
    """
    if "enum" in prop:
        return "enum"
    t = prop.get("type", "any")
    type_map = {
        "string": "str",
        "integer": "int",
        "number": "num",
        "boolean": "bool",
        "object": "obj",
        "array": "list",
    }
    short = type_map.get(t, t)
    if t == "array" and "items" in prop:
        inner = type_map.get(prop["items"].get("type", ""), "")
        if inner:
            short = f"list[{inner}]"
    return short
```

```python
def format_search_results(
    self,
    scored_tools: list[ScoredTool],
    recently_used: list[str],
    suggestions: list[str],
) -> str:
    """Format search results as a markdown table with context.

    Output format (from spec):
        Recently used: tool_a, tool_b, tool_c
        Suggested: tool_d, tool_e

        | Tool | Description | Parameters |
        |------|-------------|------------|
        | server__tool | Short description | param1 (str), param2 (int?) |

    Parameter hints are abbreviated type annotations.
    '?' suffix means optional. 'obj' signals model should describe before executing.

    Args:
        scored_tools: Ranked tools to display (already bounded to dynamic K).
        recently_used: Up to 3 recently used tool keys.
        suggestions: Up to 2 suggested tool keys not in results.

    Returns:
        Formatted markdown string.
    """
    parts: list[str] = []

    # Recently used line (max 3)
    if recently_used:
        parts.append(f"Recently used: {', '.join(recently_used[:3])}")

    # Suggestions line (max 2)
    if suggestions:
        parts.append(f"Suggested: {', '.join(suggestions[:2])}")

    # Blank line separator before table (only if we had context lines)
    if parts:
        parts.append("")

    # Table header
    parts.append("| Tool | Description | Parameters |")
    parts.append("|------|-------------|------------|")

    # Table rows
    for scored in scored_tools:
        tool = scored.tool_mapping.tool
        tool_key = scored.tool_key

        # Description: first sentence, max 80 chars
        desc = _truncate_description(tool.description or "")

        # Parameter hints
        schema = tool.inputSchema or {}
        properties = schema.get("properties", {})
        required_set = set(schema.get("required", []))

        param_parts: list[str] = []
        for param_name, param_def in properties.items():
            if not isinstance(param_def, dict):
                continue
            type_hint = self._abbreviate_param_type(param_def)
            optional_marker = "" if param_name in required_set else "?"
            param_parts.append(f"{param_name} ({type_hint}{optional_marker})")

        params_str = ", ".join(param_parts) if param_parts else "none"
        parts.append(f"| {tool_key} | {desc} | {params_str} |")

    return "\n".join(parts)
```

The existing `_truncate_description()` module-level function (already in assembler.py at L18-L27) is reused by `format_search_results`. No modification needed to that function.

The existing `assemble()` method MUST remain unchanged — it is still called by the legacy pipeline path.

**Acceptance Criteria:**
- [ ] `TieredAssembler.format_search_results()` method exists and returns a string
- [ ] Output contains "Recently used:" line when recently_used is non-empty, capped at 3 items
- [ ] Output contains "Suggested:" line when suggestions is non-empty, capped at 2 items
- [ ] Output contains markdown table with header `| Tool | Description | Parameters |`
- [ ] Tool column shows `tool_key` (server__tool format)
- [ ] Description column is truncated to first sentence or 80 chars
- [ ] Parameters column shows abbreviated type hints: str, int, num, bool, obj, list, enum
- [ ] Optional parameters have `?` suffix (not in schema's `required` array)
- [ ] Required parameters have no suffix
- [ ] Complex object parameters show as `obj` (signals model should describe before executing)
- [ ] Existing `assemble()` method is UNCHANGED
- [ ] Existing `_truncate_description()` and `_strip_descriptions()` functions are UNCHANGED
- [ ] Existing tests in `tests/test_tiered_assembler.py` still pass

**What Complete Looks Like:**
assembler.py contains all original code unchanged, plus `_abbreviate_param_type()` and `format_search_results()` methods added to `TieredAssembler`.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.retrieval.assembler import TieredAssembler; a = TieredAssembler(); print(type(a.format_search_results))"`
- Expected: `<class 'method'>`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_tiered_assembler.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: All existing tests pass (exit 0)

---

### Task 1.5: Simplify fusion.py — Query-Adaptive Alpha

**Files:**
- Modify: `src/multimcp/retrieval/fusion.py`

**Codebase References:**
- Current fusion.py: `src/multimcp/retrieval/fusion.py` — has `weighted_rrf()` and `compute_alpha()`
- Current compute_alpha signature: `compute_alpha(turn, workspace_confidence, conv_confidence, roots_changed, explicit_tool_mention)` — returns float
- Pipeline call site: `src/multimcp/retrieval/pipeline.py:L220-L226` — calls `_compute_alpha(turn=turn, workspace_confidence=ws_confidence, conv_confidence=conv_confidence, roots_changed=roots_changed, explicit_tool_mention=explicit_tool_mention)`
- Spec on alpha: "α starts high (0.85) when conversation context is thin, α decreases as conversation provides stronger signal"
- Test file: `tests/test_rrf_fusion.py`

**Implementation Details:**

The spec says: "No alpha-decay needed because there's no init-time vs turn-time distinction. Every search call runs the full blend."

`weighted_rrf()` function: **NO CHANGES**. The RRF formula `score(tool) = α / (10 + rank_env) + (1-α) / (10 + rank_conv)` stays exactly the same.

`compute_alpha()` function: **SIMPLIFY**. Remove the turn-based exponential decay. Replace with query-adaptive logic:

```python
def compute_alpha(
    turn: int = 0,
    workspace_confidence: float = 0.0,
    conv_confidence: float = 0.0,
    roots_changed: bool = False,
    explicit_tool_mention: bool = False,
) -> float:
    """Compute alpha blending weight for RRF fusion.

    Alpha = weight given to environment (workspace) ranking.
    High alpha → workspace-dominated. Low alpha → intent-dominated.

    Search-tool architecture: no turn-based decay. Alpha is determined
    purely by signal strength:
    - Strong intent (high conv_confidence) → low alpha (intent dominates)
    - Weak intent (low conv_confidence) → high alpha (workspace rescues)
    - Workspace evidence always contributes as baseline prior

    The 'turn' parameter is accepted for backward compatibility but
    is no longer used in the calculation.

    Args:
        turn: Kept for backward compatibility. Not used.
        workspace_confidence: 0.0-1.0, strength of workspace evidence.
        conv_confidence: 0.0-1.0, strength of conversation/intent signal.
        roots_changed: If True, boost workspace weight.
        explicit_tool_mention: If True with high conv_confidence, snap to intent-dominated.
    """
    # Base alpha from conversation confidence:
    # No conversation signal → alpha=0.85 (workspace dominates)
    # Full conversation signal → alpha=0.15 (intent dominates)
    base = 0.85 - 0.70 * conv_confidence
    base = max(0.15, min(0.85, base))

    # Low workspace confidence reduces env weight
    if workspace_confidence < 0.45:
        base = max(0.15, base - 0.20)

    # Explicit tool name mention with strong intent → snap to intent-dominated
    if explicit_tool_mention and conv_confidence >= 0.70:
        base = 0.15

    # Roots changed → boost workspace weight
    if roots_changed:
        base = max(base, 0.80)

    return base
```

**CRITICAL:** The function signature MUST remain identical (same parameter names, same defaults, same return type). The existing pipeline.py passes these exact keyword arguments. Existing tests in `tests/test_rrf_fusion.py` and `tests/test_alpha_query_modes.py` test this function.

**Acceptance Criteria:**
- [ ] `weighted_rrf()` function is BYTE-FOR-BYTE UNCHANGED
- [ ] `compute_alpha()` has the SAME signature (same params, same defaults, same return type)
- [ ] `compute_alpha()` no longer uses `math.exp(-0.25 * turn)` — turn parameter is ignored
- [ ] `compute_alpha(conv_confidence=0.0)` returns ~0.85 (workspace-dominated when no intent)
- [ ] `compute_alpha(conv_confidence=1.0)` returns 0.15 (intent-dominated when strong intent)
- [ ] `compute_alpha(conv_confidence=0.5)` returns ~0.50 (balanced)
- [ ] `compute_alpha(workspace_confidence=0.3, conv_confidence=0.0)` returns ≤0.65 (reduced by low ws)
- [ ] `compute_alpha(explicit_tool_mention=True, conv_confidence=0.8)` returns 0.15
- [ ] `compute_alpha(roots_changed=True)` returns ≥0.80
- [ ] `import math` at the top of the file can be REMOVED (no longer needed) or left as-is (harmless)

**What Complete Looks Like:**
fusion.py has unchanged `weighted_rrf()` and a simplified `compute_alpha()` with the same signature but no turn-based decay logic.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.retrieval.fusion import compute_alpha, weighted_rrf; print(compute_alpha(conv_confidence=0.0), compute_alpha(conv_confidence=1.0))"`
- Expected: `0.85 0.15`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_rrf_fusion.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: Exit 0 (tests pass) OR some tests may need updating if they assert turn-based decay values. If tests fail, note the specific failures — they are expected and will be addressed in Wave 3/4 test updates.

> BEFORE reporting Wave 1 as complete:
> Spawn a subagent to perform an independent review of the implementation.
> Verify all 5 modified files import correctly and all existing test suites for those files pass.

---

## Wave 2: Pipeline Core + Search Tool Tests

> **PARALLEL EXECUTION:** Both tasks in this wave run simultaneously.
>
> **Dependencies:** Wave 1 must complete.
> **File Safety:**
> - `pipeline.py`: only Task 2.1 ✓
> - `tests/test_search_tool_modes.py`: only Task 2.2 (new file) ✓

---

### Task 2.1: Update pipeline.py — Add search/describe/execute Methods

**Files:**
- Modify: `src/multimcp/retrieval/pipeline.py`

**Codebase References:**
- Current pipeline.py: `src/multimcp/retrieval/pipeline.py` — ~500 lines, `RetrievalPipeline` class with `get_tools_for_list()`, `on_tool_called()`, `rebuild_catalog()`, `set_session_roots()`, `cleanup_session()`, `record_router_describe()`
- New models from Task 1.1: `src/multimcp/retrieval/models.py` — `SearchSessionState`, `SearchEvent`, `DynamicKResult`
- New session manager from Task 1.2: `src/multimcp/retrieval/session.py` — `SearchSessionStateManager` with `record_tool_use()`, `record_describe()`, `record_search()`, `get_recently_used()`, `get_suggestion_candidates()`
- New assembler method from Task 1.4: `src/multimcp/retrieval/assembler.py` — `TieredAssembler.format_search_results()`
- Fusion functions: `src/multimcp/retrieval/fusion.py` — `weighted_rrf()`, `compute_alpha()` (signatures unchanged)
- BMXFRetriever.retrieve(): `src/multimcp/retrieval/bmx_retriever.py:L129-L181` — takes RetrievalContext + candidates → list[ScoredTool]
- Tool lookup: `self.tool_registry` is `dict[str, ToolMapping]` (reference to `mcp_proxy.tool_to_server`)
- Spec pipeline methods: spec sheet lines 296-326

**Implementation Details:**

This is the largest task. The changes are:

1. **Add new import** at the top (add to existing import block from `.models`):
```python
from .models import (
    DynamicKResult,
    RankingEvent,
    RetrievalConfig,
    RetrievalContext,
    ScoredTool,
    SearchEvent,
    SearchSessionState,
    SessionRoutingState,
    WorkspaceEvidence,
)
```

2. **Add new import** for the search session manager:
```python
from .session import SessionStateManager, SearchSessionStateManager
```

3. **Modify `__init__`** — Add a `search_session_manager` attribute alongside the existing `session_manager`:
```python
# Inside __init__, add after the existing self.session_manager = session_manager line:
self.search_session_manager = SearchSessionStateManager()
```

4. **Add `_compute_dynamic_k()` private method** to `RetrievalPipeline`:
```python
def _compute_dynamic_k(self, scored_tools: list[ScoredTool]) -> DynamicKResult:
    """Determine how many search results to return based on score distribution.

    High confidence (clear intent, strong BMXF scores): return 3 tools
    Medium confidence (ambiguous intent, moderate scores): return 4 tools
    Low confidence (vague intent, weak scores): return 5 tools + suggestion

    Confidence is determined by the max score and the gap between top scores.
    """
    if not scored_tools:
        return DynamicKResult(k=5, confidence="low", max_score=0.0,
                              suggestion="Try a more specific query")

    max_score = scored_tools[0].score
    # Score thresholds are relative to the scoring range
    if max_score >= 0.15:
        # Strong top score — high confidence
        return DynamicKResult(k=3, confidence="high", max_score=max_score)
    elif max_score >= 0.05:
        # Moderate top score — medium confidence
        return DynamicKResult(k=4, confidence="medium", max_score=max_score)
    else:
        # Weak scores — low confidence
        suggestion = "Try refining: be specific about the action and target"
        if len(suggestion) > 50:
            suggestion = suggestion[:47] + "..."
        return DynamicKResult(k=5, confidence="low", max_score=max_score,
                              suggestion=suggestion)
```

5. **Add `search()` method** to `RetrievalPipeline`:
```python
async def search(self, session_id: str, intent: str) -> str:
    """Run BMXF scoring against intent + workspace evidence.

    Returns formatted markdown table.

    Steps:
    1. Get workspace evidence from session cache
    2. Run BMXF env scoring (workspace evidence)
    3. Run BMXF conv scoring (intent as natural language query)
    4. Blend via weighted RRF
    5. Compute dynamic K
    6. Get recently used tools from search session state
    7. Get suggestion candidates
    8. Format via assembler into markdown table
    9. Log SearchEvent
    10. Record search in session state
    11. Return formatted string
    """
    import time as _time
    t0 = _time.monotonic()

    evidence = self._session_evidence.get(session_id)
    candidates = list(self.tool_registry.values())

    if not candidates:
        return "No tools available in the catalog."

    # Build query strings
    env_query = ""
    if evidence and evidence.merged_tokens:
        env_query = " ".join(evidence.merged_tokens.keys())

    conv_query = _extract_conv_terms(intent) if intent else ""
    ws_confidence = evidence.workspace_confidence if evidence else 0.0
    conv_confidence = min(1.0, len(conv_query.split()) / 10.0) if conv_query else 0.0

    # Run scoring with fallback ladder
    scored_tools: list[ScoredTool] | None = None
    fallback_tier = 1
    fusion_alpha = 0.0

    def _env_ctx() -> RetrievalContext:
        return RetrievalContext(session_id=session_id, query=env_query, query_mode="env")

    def _conv_ctx() -> RetrievalContext:
        return RetrievalContext(session_id=session_id, query=conv_query, query_mode="nl")

    # Tier 1: BMXF env + conv blend via RRF
    if (
        scored_tools is None
        and self._index_available()
        and env_query
        and conv_query
        and _HAS_FUSION
    ):
        try:
            env_ranked = await self.retriever.retrieve(_env_ctx(), candidates)
            conv_ranked = await self.retriever.retrieve(_conv_ctx(), candidates)
            fusion_alpha = _compute_alpha(
                workspace_confidence=ws_confidence,
                conv_confidence=conv_confidence,
            )
            scored_tools = _weighted_rrf(env_ranked, conv_ranked, fusion_alpha)
            fallback_tier = 1
        except Exception:
            scored_tools = None

    # Tier 2: BMXF env-only
    if scored_tools is None and self._index_available() and env_query:
        try:
            scored_tools = await self.retriever.retrieve(_env_ctx(), candidates)
            fallback_tier = 2
        except Exception:
            scored_tools = None

    # Tier 3: KeywordRetriever
    if scored_tools is None and self._keyword_retriever_available() and env_query:
        try:
            kr = getattr(self, "_keyword_retriever")
            scored_tools = await kr.retrieve(_env_ctx(), candidates)
            fallback_tier = 3
        except Exception:
            scored_tools = None

    # Tier 4: Static category defaults
    if scored_tools is None:
        project_type, confident = self._classify_project_type(evidence)
        if confident and project_type and _HAS_STATIC_CATEGORIES:
            scored_tools = self._static_category_defaults(project_type, 5)
            if scored_tools:
                fallback_tier = 4
            else:
                scored_tools = None

    # Tier 5: Frequency prior
    if scored_tools is None and self._has_frequency_prior():
        freq = self._frequency_prior_tools(5)
        if freq:
            scored_tools = freq
            fallback_tier = 5

    # Tier 6: All tool names as flat list
    if scored_tools is None:
        scored_tools = self._universal_fallback()
        fallback_tier = 6

    scored_tools.sort(key=lambda s: s.score, reverse=True)

    # Dynamic K
    dk = self._compute_dynamic_k(scored_tools)
    result_tools = scored_tools[:dk.k]
    result_keys = [s.tool_key for s in result_tools]

    # Session context
    recently_used = self.search_session_manager.get_recently_used(session_id, n=3)
    suggestions = self.search_session_manager.get_suggestion_candidates(
        session_id, [s.tool_key for s in scored_tools]
    )

    # Format
    if self.assembler is not None:
        formatted = self.assembler.format_search_results(
            result_tools, recently_used, suggestions
        )
    else:
        # Minimal fallback if no assembler
        lines = [f"- {s.tool_key}" for s in result_tools]
        formatted = "\n".join(lines) if lines else "No matching tools found."

    # Append low-confidence suggestion
    if dk.confidence == "low" and dk.suggestion:
        formatted += f"\n\nTip: {dk.suggestion}"

    latency_ms = (_time.monotonic() - t0) * 1000.0

    # Log
    event = SearchEvent(
        session_id=session_id,
        event_type="search",
        intent=intent,
        result_count=len(result_tools),
        fallback_tier=fallback_tier,
        alpha=fusion_alpha,
        latency_ms=latency_ms,
    )
    # Log via existing logger infrastructure (SearchEvent is dataclass → can asdict)
    try:
        await self.logger.log_ranking_event(event)  # type: ignore[arg-type]
    except Exception:
        pass  # Don't fail search on logging errors

    # Record in session state
    self.search_session_manager.record_search(session_id, intent, result_keys)

    return formatted
```

6. **Add `describe()` method** to `RetrievalPipeline`:
```python
async def describe(self, session_id: str, tool_key: str) -> str:
    """Return full JSON schema for a tool.

    Args:
        session_id: Session identifier for tracking.
        tool_key: Tool key in server__tool format.

    Returns:
        JSON-formatted schema string, or error message if tool not found.
    """
    mapping = self.tool_registry.get(tool_key)
    if mapping is None:
        available = sorted(self.tool_registry.keys())[:10]
        return json.dumps({
            "error": f"Tool not found: {tool_key!r}",
            "available_tools_sample": available,
        }, indent=2)

    tool = mapping.tool
    schema_info = {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": tool.inputSchema,
    }

    # Track describe in session
    self.search_session_manager.record_describe(session_id, tool_key)

    return json.dumps(schema_info, indent=2)
```

7. **Add `execute()` method** to `RetrievalPipeline`:
```python
async def execute(self, session_id: str, tool_key: str, arguments: dict) -> str:
    """Validate arguments and forward tool call to upstream MCP server.

    Args:
        session_id: Session identifier for tracking.
        tool_key: Tool key in server__tool format.
        arguments: Arguments to pass to the tool.

    Returns:
        Tool execution result as string, or error + schema on validation failure.
    """
    import time as _time
    t0 = _time.monotonic()

    mapping = self.tool_registry.get(tool_key)
    if mapping is None:
        return json.dumps({
            "error": f"Tool not found: {tool_key!r}",
        })

    # Validate arguments against inputSchema
    tool = mapping.tool
    schema = tool.inputSchema or {}
    required_params = set(schema.get("required", []))
    properties = schema.get("properties", {})

    # Check required parameters are present
    missing = required_params - set(arguments.keys())
    if missing:
        schema_info = {
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.inputSchema,
        }
        return json.dumps({
            "error": f"Missing required parameters: {sorted(missing)}",
            "schema": schema_info,
        }, indent=2)

    # Check no unknown parameters (if additionalProperties is false)
    if schema.get("additionalProperties") is False:
        unknown = set(arguments.keys()) - set(properties.keys())
        if unknown:
            schema_info = {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema,
            }
            return json.dumps({
                "error": f"Unknown parameters: {sorted(unknown)}",
                "schema": schema_info,
            }, indent=2)

    # Forward to upstream — this returns a __PROXY_CALL__ sentinel
    # that mcp_proxy.py will intercept and route to the actual server.
    # The pipeline does NOT call upstream servers directly.
    self.search_session_manager.record_tool_use(session_id, tool_key)

    latency_ms = (_time.monotonic() - t0) * 1000.0
    event = SearchEvent(
        session_id=session_id,
        event_type="execute",
        tool_key=tool_key,
        latency_ms=latency_ms,
    )
    try:
        await self.logger.log_ranking_event(event)  # type: ignore[arg-type]
    except Exception:
        pass

    return f"__PROXY_CALL__:{tool_key}"
```

8. **Modify `get_tools_for_list()`** — Add a fast path at the top of the method, BEFORE all existing logic. Insert this immediately after the `if not self.config.enabled:` block (line ~169):

```python
# Search-tool mode: when enabled, tools/list returns ONLY the search tool.
# The model discovers and invokes all other tools via search_tools.
# This replaces the bounded active-set architecture.
if self.config.enabled:
    from .routing_tool import SEARCH_TOOL_SCHEMA
    return [SEARCH_TOOL_SCHEMA]
```

**Wait — this would short-circuit ALL the existing scoring logic.** That's exactly the point. When the search-tool architecture is active, `tools/list` returns ONLY `search_tools`. The model then calls `search_tools` with an `intent` to discover tools.

However, this means the entire rest of `get_tools_for_list()` becomes dead code when `config.enabled=True`. For Phase 0 (MVP), this is correct per the spec. The existing scoring logic is still used — it's just called via `pipeline.search()` instead of `get_tools_for_list()`.

**IMPORTANT:** Do NOT delete the existing `get_tools_for_list()` body below the new early return. It serves as:
- Backward-compatible fallback when `config.enabled=False`
- Reference implementation for the scoring pipeline reused in `search()`
- Test infrastructure still calls it

9. **Modify `cleanup_session()`** — Add cleanup for the search session manager. After the existing `self.session_manager.cleanup_session(session_id)` call at the bottom:
```python
self.search_session_manager.cleanup_session(session_id)
```

10. **Add `json` import** if not already present (check: it's not currently imported in pipeline.py). Add to the import block at the top:
```python
import json  # Already present at line 3
```
(Verify: yes, `json` is already imported at line 3 of pipeline.py. No change needed.)

**Acceptance Criteria:**
- [ ] `pipeline.search(session_id, intent)` exists, is async, returns a string (markdown table)
- [ ] `pipeline.describe(session_id, tool_key)` exists, is async, returns a string (JSON schema)
- [ ] `pipeline.execute(session_id, tool_key, arguments)` exists, is async, returns a string (proxy sentinel or error+schema)
- [ ] `search()` runs the 6-tier fallback ladder (same as get_tools_for_list's scoring)
- [ ] `search()` uses dynamic K (3/4/5 results based on confidence)
- [ ] `search()` includes recently_used and suggestions via SearchSessionStateManager
- [ ] `search()` formats output via assembler.format_search_results()
- [ ] `describe()` returns JSON with name, description, inputSchema
- [ ] `describe()` returns error JSON for unknown tool keys
- [ ] `execute()` validates required params against inputSchema
- [ ] `execute()` returns error + full schema on validation failure (so model can retry without separate describe)
- [ ] `execute()` returns `__PROXY_CALL__:{tool_key}` sentinel on success (mcp_proxy intercepts this)
- [ ] `get_tools_for_list()` returns `[SEARCH_TOOL_SCHEMA]` when config.enabled=True
- [ ] `get_tools_for_list()` returns all tools when config.enabled=False (unchanged behavior)
- [ ] `cleanup_session()` also cleans up search_session_manager
- [ ] All existing pipeline methods still exist and function

**What Complete Looks Like:**
pipeline.py has three new public methods (search, describe, execute), a modified get_tools_for_list that short-circuits to return only search_tools when enabled, and a new SearchSessionStateManager instance. All existing code remains for backward compatibility.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.retrieval.pipeline import RetrievalPipeline; print([m for m in dir(RetrievalPipeline) if m in ('search', 'describe', 'execute')])"`
- Expected: `['describe', 'execute', 'search']`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_retrieval_pipeline.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: Exit 0 (existing tests pass — they test config.enabled=False path which is unchanged)

---

### Task 2.2: Create tests/test_search_tool_modes.py

**Files:**
- Create: `tests/test_search_tool_modes.py`

**Codebase References:**
- New routing_tool exports (from Task 1.3): `src/multimcp/retrieval/routing_tool.py` — `SEARCH_TOOL_NAME`, `SEARCH_TOOL_SCHEMA`, `handle_search_tool_call()`
- New assembler method (from Task 1.4): `src/multimcp/retrieval/assembler.py` — `TieredAssembler.format_search_results()`
- Existing test patterns: `tests/test_routing_tool.py` — uses MagicMock for ToolMapping, _make_tool_mapping helper
- ScoredTool: `src/multimcp/retrieval/models.py:L16-L21`
- pytest config: `pyproject.toml` — `asyncio_mode = "auto"`, `pythonpath = ["."]`

**Implementation Details:**

Create a comprehensive test file covering:

1. **SEARCH_TOOL_SCHEMA constants and shape:**
   - `test_search_tool_name()` — SEARCH_TOOL_NAME == "search_tools"
   - `test_search_tool_schema_is_static()` — SEARCH_TOOL_SCHEMA is a types.Tool
   - `test_search_tool_schema_has_four_properties()` — intent, name, describe, arguments
   - `test_search_tool_schema_no_required()` — no "required" key (all optional, mode inferred)
   - `test_search_tool_schema_describe_default_false()` — describe property default is False
   - `test_search_tool_schema_arguments_default_empty()` — arguments property default is {}

2. **handle_search_tool_call mode routing:**
   - `test_search_mode_calls_pipeline_search()` — intent present, no name → pipeline.search() called
   - `test_describe_mode_calls_pipeline_describe()` — name + describe=True → pipeline.describe() called
   - `test_execute_mode_calls_pipeline_execute()` — name + arguments → pipeline.execute() called
   - `test_bare_name_calls_describe()` — name only, no describe flag, no args → pipeline.describe()
   - `test_no_intent_no_name_returns_error()` — empty arguments → error string
   - `test_intent_and_name_prefers_search()` — both intent and name → spec says intent without name for search, so if name is present it's not search mode. This case: name + intent but no describe/args → describe mode (name takes precedence)

3. **format_search_results output:**
   - `test_format_empty_tools()` — empty scored list → just header, no rows
   - `test_format_single_tool()` — one tool → one table row
   - `test_format_recently_used_line()` — recently_used list appears as "Recently used: ..."
   - `test_format_suggestions_line()` — suggestions list appears as "Suggested: ..."
   - `test_format_recently_used_capped_at_3()` — only first 3 shown
   - `test_format_suggestions_capped_at_2()` — only first 2 shown
   - `test_format_param_types()` — str, int, bool, obj correctly abbreviated
   - `test_format_optional_params_have_question_mark()` — optional params show "?"
   - `test_format_required_params_no_suffix()` — required params have no "?"

Use `unittest.mock.AsyncMock` for the pipeline object in handle_search_tool_call tests. Use MagicMock for ToolMapping in assembler tests (same pattern as `tests/test_routing_tool.py`).

**Acceptance Criteria:**
- [ ] Test file has at least 15 test functions covering all three areas
- [ ] All tests pass when run in isolation
- [ ] Tests use `AsyncMock` for pipeline methods (search, describe, execute)
- [ ] Tests verify exact mode routing logic from the spec
- [ ] Tests verify markdown table format matches spec

**What Complete Looks Like:**
A self-contained test file at `tests/test_search_tool_modes.py` with comprehensive coverage of the new search tool schema, mode routing, and markdown formatting.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_search_tool_modes.py -x -v 2>&1 | tail -5`
- Expected: All tests pass

> BEFORE reporting Wave 2 as complete:
> Spawn a subagent to perform an independent review of the implementation.

---

## Wave 3: Proxy Wiring + Pipeline Integration Tests

> **PARALLEL EXECUTION:** Both tasks in this wave run simultaneously.
>
> **Dependencies:** Wave 2 must complete.
> **File Safety:**
> - `mcp_proxy.py`: only Task 3.1 ✓
> - `tests/test_search_pipeline.py`: only Task 3.2 (new file) ✓

---

### Task 3.1: Update mcp_proxy.py — Wire search_tools into Proxy

**Files:**
- Modify: `src/multimcp/mcp_proxy.py`

**Codebase References:**
- Current `_list_tools`: `src/multimcp/mcp_proxy.py:L205-L220` — delegates to retrieval_pipeline.get_tools_for_list() or returns all tools
- Current `_call_tool` routing-tool dispatch: `src/multimcp/mcp_proxy.py:L259-L308` — checks for `ROUTING_TOOL_NAME`, dispatches via `handle_routing_call`
- New routing_tool exports (from Task 1.3): `SEARCH_TOOL_NAME`, `SEARCH_TOOL_SCHEMA`, `handle_search_tool_call()`
- New pipeline methods (from Task 2.1): `pipeline.search()`, `pipeline.describe()`, `pipeline.execute()`
- Session ID derivation: `src/multimcp/mcp_proxy.py:L185-L195` — `_get_session_id()`

**Implementation Details:**

**Change 1:** In `_list_tools` method (around line 205), modify the retrieval pipeline branch. Currently:
```python
if self.retrieval_pipeline is not None:
    session_id = self._get_session_id()
    # ... build conversation context ...
    tools = await self.retrieval_pipeline.get_tools_for_list(session_id, conversation_context)
    # ... hash tracking ...
    return types.ServerResult(tools=tools)
```

Replace with:
```python
if self.retrieval_pipeline is not None and self.retrieval_pipeline.config.enabled:
    # Search-tool architecture: return ONLY the search tool.
    # The model discovers and invokes all tools via search_tools.
    from src.multimcp.retrieval.routing_tool import SEARCH_TOOL_SCHEMA
    return types.ServerResult(tools=[SEARCH_TOOL_SCHEMA])

if self.retrieval_pipeline is not None:
    session_id = self._get_session_id()
    # ... existing conversation context and get_tools_for_list code unchanged ...
```

This preserves the existing `get_tools_for_list` path for when `config.enabled=False`.

**Change 2:** In `_call_tool` method, add a NEW dispatch block for `search_tools` BEFORE the existing routing tool dispatch block (which handles `request_tool`). Insert before line 259 (the `try: from src.multimcp.retrieval.routing_tool import ROUTING_TOOL_NAME` block):

```python
# Search tool dispatch — handles the search_tools unified entry point
try:
    from src.multimcp.retrieval.routing_tool import SEARCH_TOOL_NAME as _SEARCH_NAME
    from src.multimcp.retrieval.routing_tool import handle_search_tool_call as _handle_search
    _has_search_tool = True
except ImportError:
    _has_search_tool = False

if _has_search_tool and tool_name == _SEARCH_NAME and self.retrieval_pipeline is not None:
    session_id = self._get_session_id()
    result_text = await _handle_search(
        self.retrieval_pipeline, session_id, arguments
    )

    # Check for __PROXY_CALL__ sentinel from execute mode
    if result_text.startswith("__PROXY_CALL__:"):
        actual_tool_name = result_text.split(":", 1)[1]
        proxy_req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name=actual_tool_name,
                arguments=arguments.get("arguments", {}),
            ),
        )
        proxy_result = await self._call_tool(proxy_req, _skip_pipeline_record=True)

        # Record usage in search session state
        if self.retrieval_pipeline is not None:
            self.retrieval_pipeline.search_session_manager.record_tool_use(
                session_id, actual_tool_name
            )

        return proxy_result

    # Non-proxy result (search or describe mode) — return as text content
    return types.ServerResult(
        content=[types.TextContent(type="text", text=result_text)]
    )
```

**Change 3:** The existing routing tool dispatch block (for `request_tool`) remains UNCHANGED. It continues to handle the legacy routing tool for backward compatibility. When `config.enabled=True`, `request_tool` is never in `tools/list` so the model won't call it, but the code path is harmless.

**Acceptance Criteria:**
- [ ] `_list_tools` returns `[SEARCH_TOOL_SCHEMA]` when `retrieval_pipeline.config.enabled=True`
- [ ] `_list_tools` falls through to existing `get_tools_for_list` path when `config.enabled=False`
- [ ] `_list_tools` returns all tools when `retrieval_pipeline is None`
- [ ] `_call_tool` dispatches `search_tools` calls to `handle_search_tool_call()`
- [ ] Execute mode: `__PROXY_CALL__` sentinel triggers proxy forwarding to actual tool
- [ ] Search/describe mode: returns `TextContent` with the result string
- [ ] Existing `request_tool` dispatch is UNCHANGED
- [ ] All other tool calls route through the existing code path unchanged

**What Complete Looks Like:**
mcp_proxy.py has a new fast-path in `_list_tools` for search-tool mode, and a new dispatch block in `_call_tool` for `search_tools` calls. All existing routing-tool and direct-tool-call paths are preserved.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.mcp_proxy import MCPProxyServer; print('OK')"`
- Expected: `OK`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/proxy_test.py -x -q 2>/dev/null; echo "exit: $?"`
- Expected: Exit 0

---

### Task 3.2: Create tests/test_search_pipeline.py

**Files:**
- Create: `tests/test_search_pipeline.py`

**Codebase References:**
- Pipeline class (from Task 2.1): `src/multimcp/retrieval/pipeline.py` — `RetrievalPipeline` with `search()`, `describe()`, `execute()`
- SearchSessionStateManager (from Task 1.2): `src/multimcp/retrieval/session.py`
- Existing pipeline test patterns: `tests/test_retrieval_pipeline.py` — `_make_tool()`, `_make_mapping()` helpers, `RetrievalConfig`, `PassthroughRetriever`, `NullLogger`
- Assembler (from Task 1.4): `TieredAssembler` with `format_search_results()`

**Implementation Details:**

Create a comprehensive test file modeled after `tests/test_retrieval_pipeline.py`:

1. **Helper functions** — Copy `_make_tool()` and `_make_mapping()` from `tests/test_retrieval_pipeline.py` verbatim.

2. **Pipeline factory helper:**
```python
def _make_search_pipeline(registry, assembler=None, **config_overrides):
    config = RetrievalConfig(enabled=True, **config_overrides)
    return RetrievalPipeline(
        retriever=PassthroughRetriever(),
        session_manager=SessionStateManager(config),
        logger=NullLogger(),
        config=config,
        tool_registry=registry,
        assembler=assembler,
    )
```

3. **Test classes:**

**`TestGetToolsForListSearchMode`:**
- `test_enabled_returns_only_search_tool()` — config.enabled=True → returns exactly 1 tool named "search_tools"
- `test_disabled_returns_all_tools()` — config.enabled=False → returns all registry tools

**`TestPipelineSearch`:**
- `test_search_returns_string()` — search() returns a string
- `test_search_with_assembler_returns_markdown_table()` — output contains "| Tool |" header
- `test_search_empty_catalog_returns_message()` — empty registry → "No tools available"
- `test_search_records_in_session()` — after search, search_session_manager has the query
- `test_search_includes_recently_used()` — after record_tool_use + search, recently_used appears in output
- `test_search_bounded_results()` — never more than 5 tools in table

**`TestPipelineDescribe`:**
- `test_describe_returns_json()` — valid tool → JSON with name, description, inputSchema
- `test_describe_unknown_tool()` — unknown key → JSON with error field
- `test_describe_records_in_session()` — after describe, tool appears in tools_described

**`TestPipelineExecute`:**
- `test_execute_valid_returns_proxy_sentinel()` — valid args → `__PROXY_CALL__:{key}`
- `test_execute_missing_required_returns_error_with_schema()` — missing required param → JSON with error + schema
- `test_execute_unknown_tool_returns_error()` — unknown key → JSON with error
- `test_execute_records_usage()` — after execute, tool appears in tools_used

**`TestSearchSessionTracking`:**
- `test_session_isolation()` — two sessions don't share state
- `test_cleanup_clears_search_state()` — cleanup_session removes search session data

**Acceptance Criteria:**
- [ ] At least 15 test functions
- [ ] Tests cover search, describe, execute, and get_tools_for_list modes
- [ ] Tests verify session state tracking
- [ ] Tests verify markdown output format
- [ ] Tests verify error cases (unknown tools, missing params)
- [ ] All tests pass

**What Complete Looks Like:**
A comprehensive test file at `tests/test_search_pipeline.py` with full coverage of the new pipeline methods.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/test_search_pipeline.py -x -v 2>&1 | tail -5`
- Expected: All tests pass

> BEFORE reporting Wave 3 as complete:
> Spawn a subagent to perform an independent review of the implementation.

---

## Wave 4: Integration Wiring

> **1 task in this wave.**
>
> **Dependencies:** Wave 3 must complete.
> **File Safety:**
> - `multi_mcp.py`: only Task 4.1 ✓

---

### Task 4.1: Update multi_mcp.py — Simplified Pipeline Wiring

**Files:**
- Modify: `src/multimcp/multi_mcp.py`

**Codebase References:**
- Current pipeline wiring: `src/multimcp/multi_mcp.py:L280-L360` — builds RetrievalConfig, creates retriever, creates pipeline
- Current pipeline construction: `src/multimcp/multi_mcp.py:L340-L350` — `RetrievalPipeline(retriever=..., session_manager=..., logger=..., config=..., tool_registry=..., telemetry_scanner=..., rolling_metrics=...)`
- New assembler import needed: `from src.multimcp.retrieval.assembler import TieredAssembler`
- Pipeline constructor (from Task 2.1): now also uses `assembler` parameter (already in `__init__` signature: `assembler: Optional["TieredAssembler"] = None`)

**Implementation Details:**

The pipeline wiring in `run()` method needs one addition: pass a `TieredAssembler` instance to the pipeline constructor so that `search()` can format markdown tables.

**Change 1:** In the pipeline construction block (around line 340-350), add the assembler. Find this line:

```python
self.proxy.retrieval_pipeline = RetrievalPipeline(
    retriever=retriever,
    session_manager=SessionStateManager(retrieval_config),
    logger=retrieval_logger,
    config=retrieval_config,
    tool_registry=self.proxy.tool_to_server,
    telemetry_scanner=telemetry_scanner,
    rolling_metrics=rolling_metrics,
)
```

Add `assembler=TieredAssembler()` to the constructor call:

```python
from src.multimcp.retrieval.assembler import TieredAssembler

self.proxy.retrieval_pipeline = RetrievalPipeline(
    retriever=retriever,
    session_manager=SessionStateManager(retrieval_config),
    logger=retrieval_logger,
    config=retrieval_config,
    tool_registry=self.proxy.tool_to_server,
    assembler=TieredAssembler(),
    telemetry_scanner=telemetry_scanner,
    rolling_metrics=rolling_metrics,
)
```

The `TieredAssembler` import should be placed alongside the other retrieval imports in the same `run()` block (around line 280) rather than at module level, to keep the lazy-import pattern consistent. Add it after the existing `from src.multimcp.retrieval.session import SessionStateManager` line:

```python
from src.multimcp.retrieval.assembler import TieredAssembler
```

**Change 2:** No other changes needed. The pipeline's `get_tools_for_list()` now handles the search-tool fast path internally (from Task 2.1). The proxy's `_list_tools()` handles the fast path at its level (from Task 3.1). The wiring in `multi_mcp.py` just needs to ensure the assembler is available.

**NOTE:** The `ranker` parameter is NOT passed and remains `None`. The search-tool architecture does not use the `RelevanceRanker` for final ordering — BMXF scores determine rank directly. This is correct per the spec.

**Acceptance Criteria:**
- [ ] `TieredAssembler()` is passed as `assembler` parameter to `RetrievalPipeline` constructor
- [ ] Import of `TieredAssembler` is in the lazy-import block inside `run()`, not at module level
- [ ] All other pipeline construction parameters are UNCHANGED
- [ ] Server starts successfully with `config.enabled=True` in retrieval settings
- [ ] Server starts successfully with `config.enabled=False` (backward compat)

**What Complete Looks Like:**
multi_mcp.py has one additional import and one additional constructor argument. Everything else unchanged.

**Verification:**
- Run: `cd /home/tanner/Projects/multi-mcp && python -c "from src.multimcp.multi_mcp import MultiMCP; print('OK')"`
- Expected: `OK`
- Run: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -10`
- Expected: All tests pass (or pre-existing failures only)

> BEFORE reporting Wave 4 as complete:
> Spawn a subagent to perform an independent review of the implementation.
> Run the full test suite: `cd /home/tanner/Projects/multi-mcp && python -m pytest tests/ -x -q --timeout=60`

---

## Post-Implementation Verification Checklist

After all 4 waves are complete, verify the following end-to-end:

1. **Import chain:** `python -c "from src.multimcp.retrieval import RetrievalPipeline; from src.multimcp.retrieval.routing_tool import SEARCH_TOOL_SCHEMA, handle_search_tool_call; from src.multimcp.retrieval.session import SearchSessionStateManager; print('All imports OK')"`

2. **Search tool schema stability:** `python -c "from src.multimcp.retrieval.routing_tool import SEARCH_TOOL_SCHEMA; import json; s1 = json.dumps(SEARCH_TOOL_SCHEMA.inputSchema, sort_keys=True); s2 = json.dumps(SEARCH_TOOL_SCHEMA.inputSchema, sort_keys=True); assert s1 == s2; print('Schema is deterministic')"`

3. **Backward compatibility:** `python -m pytest tests/test_retrieval_pipeline.py tests/test_routing_tool.py tests/test_tiered_assembler.py tests/test_retrieval_session.py tests/test_rrf_fusion.py tests/test_retrieval_models.py -x -q`

4. **New tests:** `python -m pytest tests/test_search_tool_modes.py tests/test_search_pipeline.py -x -v`

5. **Full suite:** `python -m pytest tests/ -x -q --timeout=60`

---

## Optional Enhancement: Session Boost from Promote/Demote Logic

The spec notes: "If during planning; a way to optionally utilize this existing logic or reuse it in a helpful way that isn't just complete removal is identified — include it in the plan as an optional feature."

The existing `SessionStateManager.promote()`/`demote()` hysteresis logic can be repurposed as a **search result boost mechanism**:

- When a tool has been described AND executed in the same session (analogous to "promoted" in the old model), it gets a score boost in subsequent searches. This is useful because the model has invested context window tokens in understanding this tool's schema.
- Implementation: In `pipeline.search()`, after BMXF scoring, check `search_session_manager.get_or_create(session_id).tools_described` and apply a +0.05 score boost to any scored tool that appears in both `tools_described` and `tools_used`. This biases search results toward tools the model has already committed to using, reducing unnecessary re-discovery.
- This is a post-MVP enhancement and is NOT required for Phase 0 shipping.
