# Roadmap — Multi-MCP Phase 2: BMXF Routing

**9 phases** | **32 requirements mapped** | Gap closure phases 7-9 address 15 audit findings from `docs/implementation-audit-final.md`

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|------------------|
| 1 | Foundations | BMXF scorer in shadow, all existing tests pass | SCORE-01–04, CATALOG-01–04, WIRE-01–02, TEST-01–02 | 3 |
| 2 | Safe Lexical MVP | 2/3 | In Progress|  |
| 3 | Turn-by-Turn Adaptive | 4/4 | Complete   | 2026-03-29 |
| 4 | Rollout Hardening | 2/4 | In Progress|  |
| 5 | Post-GA Learning | PPMI-weighted scoring; exploration injection | v2 requirements (PPMI, exploration, co-occurrence) | ⛔ BLOCKED |
| 6 | Verification & Compliance | All 6 core invariants enforced by automated tests; trust boundary audit passes | VERIFY-01–06 | ⛔ BLOCKED |
| **7** | **Core Pipeline Wiring** | **Scoring/retrieval paths execute end-to-end per source plan** | **ROUTER-01, FALLBACK-01–02, FUSION-01–03, SCORE-03–04** | **Gap Closure** |
| **8** | **Session State & Turn Boundary** | **Session isolation, turn-boundary discipline, state management** | **SESSION-01–04, OBS-02** | **Gap Closure** |
| **9** | **Rollout Activation & Observability** | **Pipeline enabled via config, rollout gates correct, logging live** | **WIRE-01–02, CATALOG-04** | **Gap Closure** |

---

## Phase 1: Foundations

**Goal:** BMXF scores computed in shadow mode. Existing behavior unchanged. All 46+ existing tests pass.

**Requirements:** SCORE-01, SCORE-02, SCORE-03, SCORE-04, CATALOG-01, CATALOG-02, CATALOG-03, CATALOG-04, WIRE-01, WIRE-02, TEST-01, TEST-02

**Plans:** 2 plans

Plans:
- [ ] 01-01-PLAN.md — BMXIndex copy + BMXF wrapper + extended models.py + catalog.py
- [ ] 01-02-PLAN.md — BMXFRetriever + __init__ exports + pipeline wiring + tests

**New files:**
- `src/multimcp/retrieval/bmx_index.py` — BMXIndex + BMXF field wrapper
- `src/multimcp/retrieval/bmx_retriever.py` — BMXFRetriever(ToolRetriever)
- `src/multimcp/retrieval/catalog.py` — ToolCatalogSnapshot, ToolDoc, build_snapshot()
- `tests/test_bmx_retriever.py`
- `tests/test_catalog_snapshot.py`

**Updated files:**
- `src/multimcp/retrieval/models.py` — ToolDoc, ToolCatalogSnapshot, SessionRoutingState, RankingEvent, updated RetrievalConfig
- `src/multimcp/retrieval/__init__.py` — export new classes
- `src/multimcp/mcp_proxy.py` — wire retrieval_pipeline
- `src/multimcp/multi_mcp.py` — instantiate pipeline with config

**Success criteria:**
1. `BMXFRetriever.rebuild_index(registry)` produces a BMXF-scored catalog for all 168 tools in <100ms
2. Shadow mode logs ranking decisions without changing tool exposure (all existing tests pass unmodified)
3. `ToolCatalogSnapshot.schema_hash` is stable across identical registry states and changes on any tool schema update

---

## Phase 2: Safe Lexical MVP

**Goal:** Bounded turn-zero active set derived from roots. No full-catalog exposure. Recall@15 > baseline (PassthroughRetriever).

**Requirements:** TELEM-01, TELEM-02, TELEM-03, TELEM-04, ROUTER-01, ROUTER-02, ROUTER-03, ROUTER-04, FALLBACK-01, FALLBACK-02, OBS-01, OBS-02, TEST-03, TEST-04

**Plans:** 2/3 plans executed

Plans:
- [x] 02-01-PLAN.md — Telemetry subpackage (scanner, evidence, tokens) + test_telemetry_scanner.py
- [x] 02-02-PLAN.md — RoutingTool + assembler routing-tool tier + test_routing_tool.py
- [x] 02-03-PLAN.md — FileRetrievalLogger + pipeline bounded-K + mcp_proxy routing dispatch

**New files:**
- `src/multimcp/retrieval/telemetry/__init__.py`
- `src/multimcp/retrieval/telemetry/scanner.py` — allowlisted root scanner
- `src/multimcp/retrieval/telemetry/evidence.py` — RootEvidence, WorkspaceEvidence
- `src/multimcp/retrieval/telemetry/tokens.py` — signal → typed sparse token generation
- `src/multimcp/retrieval/routing_tool.py` — synthetic MCP routing tool
- `tests/test_telemetry_scanner.py`
- `tests/test_routing_tool.py`

**Updated files:**
- `src/multimcp/retrieval/assembler.py` — routing-tool tier (optional routing_tool_schema param)
- `src/multimcp/retrieval/pipeline.py` — bounded K, routing tool, fallback chain, RankingEvent emission
- `src/multimcp/retrieval/logging.py` — FileRetrievalLogger
- `src/multimcp/mcp_proxy.py` — ROUTING_TOOL_KEY dispatch in _call_tool()

**Success criteria:**
1. Session init exposes ≤20 tools directly; remaining tools accessible only via routing tool (never full catalog dump)
2. Telemetry scanner reads only allowlisted files within declared roots; `.env*`, SSH keys, arbitrary source files blocked
3. Scan completes within 150ms hard timeout for 10K-entry monorepo; triggers partial evidence mode on timeout

---

## Phase 3: Turn-by-Turn Adaptive

**Goal:** Describe rate improves over Phase 2 baseline. Active-set churn bounded. p95 scoring latency <50ms.

**Requirements:** FUSION-01, FUSION-02, FUSION-03, SESSION-01, SESSION-02, SESSION-03, SESSION-04, TELEM-05, TEST-05, TEST-06

**New files:**
- `src/multimcp/retrieval/fusion.py` — weighted RRF, alpha-decay blend
- `src/multimcp/retrieval/telemetry/monitor.py` — change detection, adaptive polling, debounce
- `tests/test_rrf_fusion.py`
- `tests/test_session_promote_demote.py`

**Updated files:**
- `src/multimcp/retrieval/session.py` — promote/demote hysteresis (replaces monotonic guarantee)
- `src/multimcp/retrieval/pipeline.py` — conversation query extraction, RRF blend, dynamic K

**Success criteria:**
1. `RankingEvent` emitted every turn with correct alpha value: 0.85 at turn 0 decaying to ~0.15 at turn 10+
2. Promote/demote fires at most 3 demotions per turn; promoted tools stay in active set for ≥2 turns before re-evaluation
3. p95 scoring latency <50ms for 500-tool corpus; adaptive polling freezes to 15s minimum when p95 >75ms
4. `SessionRoutingState` is per-session isolated — no state leaks between concurrent sessions

---

## Phase 4: Rollout Hardening

**Goal:** All rollout gates pass in shadow. Alerting and dashboards operational.

**Requirements:** Shadow→canary feature flags, rollout gates, alert thresholds, replay regression structure

**Plans:** 2/4 plans executed

Plans:
- [x] 04-01-PLAN.md — Canary rollout config (RetrievalConfig fields) + session assignment utility + tests
- [x] 04-02-PLAN.md — Replay evaluator module (JSONL → metrics → cutover gate check) + tests
- [x] 04-03-PLAN.md — Pipeline canary routing + RankingEvent group labeling + logging alerts + tests
- [x] 04-04-PLAN.md — Metric aggregation + alert thresholds + operator runbook + tests

**New files:**
- `src/multimcp/retrieval/rollout.py` — Deterministic canary session assignment
- `src/multimcp/retrieval/replay.py` — Offline replay evaluator with gate checker
- `src/multimcp/retrieval/metrics.py` — Rolling metric aggregation + alert checker
- `tests/test_rollout.py`
- `tests/test_replay_evaluator.py`
- `tests/test_canary_pipeline.py`
- `tests/test_metrics.py`
- `docs/OPERATOR-RUNBOOK.md`

**Updated files:**
- `src/multimcp/retrieval/models.py` — canary_percentage, rollout_stage, group on RankingEvent
- `src/multimcp/retrieval/pipeline.py` — Canary routing in get_tools_for_list()
- `src/multimcp/retrieval/logging.py` — log_alert() method

**Success criteria:**
1. Recall@15 ≥5% improvement over `KeywordRetriever` baseline in shadow mode
2. Describe rate ≥20% drop vs PassthroughRetriever baseline (routing tool used less often)
3. All cutover gates pass: Tier 5-6 <5%, p95 <50ms, no trust-boundary violations

---

## Phase 5: Post-GA Learning — ⛔ BLOCKED

**Goal:** PPMI-weighted token scoring replaces static heuristics after sufficient session data.

**Requirements:** v2 requirements (PPMI reweighting, exploration injection, co-occurrence)

**Status:** BLOCKED — Plans were written against the deviated codebase. Must re-plan after gap closure phases 7-9 complete and codebase aligns with source of truth (`docs/PHASE2-SYNTHESIZED-PLAN.md`).

**Plans:** 3 plans

Plans:
- [ ] 05-01-PLAN.md — Co-occurrence tracker + PPMI computation module + tests
- [ ] 05-02-PLAN.md — Exploration injection (inverse-frequency weighted) + tests
- [ ] 05-03-PLAN.md — PPMI integration into retriever + pipeline exploration wiring + tests

**New files:**
- `src/multimcp/retrieval/learning.py` — CooccurrenceTracker, compute_ppmi, reweight_tokens
- `src/multimcp/retrieval/exploration.py` — ExplorationInjector with inverse-frequency weighting
- `tests/test_learning.py`
- `tests/test_exploration.py`
- `tests/test_ppmi_integration.py`

**Updated files:**
- `src/multimcp/retrieval/models.py` — enable_ppmi, enable_exploration, exploration_tools
- `src/multimcp/retrieval/bmx_retriever.py` — load_ppmi() method
- `src/multimcp/retrieval/pipeline.py` — Exploration injection integration

**Success criteria:**
1. PPMI-reweighted token scores show measurable improvement over static weights (offline eval on replay dataset)
2. Exploration injection (2 slots) does not increase describe rate vs Phase 3 baseline

---

## Phase 6: Verification & Compliance — ⛔ BLOCKED

**Goal:** All 6 core invariants enforced by automated tests. Trust boundary audit passes. Full-stack lifecycle regression suite guards against future regressions.

**Requirements:** VERIFY-01, VERIFY-02, VERIFY-03, VERIFY-04, VERIFY-05, VERIFY-06

**Status:** BLOCKED — Tests must verify the corrected codebase, not the deviated one. Must re-plan after gap closure phases 7-9 complete.

**Plans:** 1 plan

Plans:
- [ ] 06-01-PLAN.md — Invariant compliance suite + trust boundary audit + lifecycle regression tests

**New files:**
- `tests/test_invariant_compliance.py` — Automated enforcement of all 6 core invariants
- `tests/test_trust_boundary.py` — Trust boundary and privacy audit
- `tests/test_lifecycle_regression.py` — Full-stack multi-session lifecycle regression suite

**Updated files:**
- None — this phase is test-only

**Success criteria:**
1. No code path can expose more than 20 tools directly (full exposure safety invariant)
2. Telemetry scanner provably cannot read outside declared roots or access denied patterns
3. Active set does not mutate mid-turn under any pipeline code path
4. Every turn is pinned to exactly one ToolCatalogSnapshot.version
5. SessionRoutingState is never shared across concurrent sessions
6. Every fallback tier (1-6) produces a bounded, valid active set with routing tool

---

## Phase 7: Core Pipeline Wiring — Gap Closure

**Goal:** All scoring and retrieval paths execute end-to-end per source plan. The core BMXF scoring, telemetry-driven turn-zero ranking, RRF fusion, and 6-tier fallback ladder are wired into the live pipeline — no dead code, no alphabetical fallback.

**Gap Closure:** F-01, F-03, F-04, F-05, F-13, F-14, F-15
**Requirements:** ROUTER-01, FALLBACK-01, FALLBACK-02, FUSION-01, FUSION-02, FUSION-03, SCORE-03, SCORE-04

Plans:
- [x] 07-01-PLAN.md — Fix routing dispatch + wire telemetry/retrieve/RRF/fallback ladder + fix aliases/top_k/alpha_override

**Updated files:**
- `src/multimcp/mcp_proxy.py` — routing tool dispatch fix (F-01)
- `src/multimcp/retrieval/pipeline.py` — wire retrieve(), weighted_rrf(), compute_alpha(), 6-tier fallback, fallback_tier tracking (F-03, F-04, F-05)
- `src/multimcp/retrieval/bmx_retriever.py` — fix NAMESPACE_ALIASES, set alpha_override=0.5 for env query (F-13, F-14)
- `src/multimcp/retrieval/models.py` — top_k default 15 (F-15)

**Success criteria (from source plan):**
1. Routing tool callable end-to-end: model calls `"request_tool"` → proxy dispatches → response returned (source plan line 999, Core Invariant line 321)
2. Turn-zero ranking uses telemetry-derived env query via BMXF, not alphabetical sort (source plan lines 273-293)
3. `weighted_rrf()` and `compute_alpha()` called every turn in `get_tools_for_list()` (source plan lines 295-307)
4. All 6 fallback tiers reachable; no tier exposes more than 20 direct tools; Tier 6 = universal 12-tool set + routing tool (source plan lines 889-920)
5. `fallback_tier` in `RankingEvent` reflects actual tier used (source plan line 482)

---

## Phase 8: Session State & Turn Boundary — Gap Closure

**Goal:** Session isolation enforced with real session IDs. All promote/demote operations and tools/list_changed notifications occur at turn boundaries only. SessionRoutingState tracks per-session state. Catalog snapshot pinning enforced.

**Gap Closure:** F-02, F-07, F-08, F-10, F-12
**Requirements:** SESSION-01, SESSION-02, SESSION-03, SESSION-04, OBS-02
**Depends on:** Phase 7 (scoring must work for rank-based promotion/demotion)

Plans:
- [ ] 08-01-PLAN.md — Wire real session IDs + turn-boundary guard + SessionRoutingState + catalog pinning + demote/promote wiring

**Updated files:**
- `src/multimcp/mcp_proxy.py` — extract real session_id, turn-boundary emit of tools/list_changed (F-02, F-07)
- `src/multimcp/retrieval/pipeline.py` — instantiate SessionRoutingState, catalog pinning, demote() calls, K-2 promotion (F-08, F-10, F-12)

**Success criteria (from source plan):**
1. Each MCP session gets a unique session_id; no hardcoded "default" (source plan lines 324-325)
2. Active set and router enum do not change during a model turn (Core Invariant line 323: "Mid-turn stability")
3. `SessionRoutingState` instantiated per session with all fields from source plan lines 450-467
4. Every turn pinned to one `ToolCatalogSnapshot.version` (Core Invariant line 324: "Snapshot pinning")
5. `demote()` called at turn boundaries; rank below K+3 for 2 consecutive turns, max 3 per turn (source plan line 305)
6. Promote: rank within K-2 OR used via router 2/3 last turns (source plan line 304)

---

## Phase 9: Rollout Activation & Observability — Gap Closure

**Goal:** Pipeline enabled via YAML config path (not hardcoded disabled). Shadow/canary rollout wired into server runtime. Cutover gates compute correct metrics with correct semantics.

**Gap Closure:** F-06, F-09, F-11
**Requirements:** WIRE-01, WIRE-02, CATALOG-04
**Depends on:** Phase 8 (pipeline must produce correct results before enabling)

Plans:
- [x] 09-01-PLAN.md — YAML retrieval config + config-driven pipeline init + Recall@15 + describe_rate gate fix + ALERT_RESCORE_RATE

**Updated files:**
- `src/multimcp/yaml_config.py` — expose retrieval config fields (F-06, F-09)
- `src/multimcp/multi_mcp.py` — config-driven pipeline init, FileRetrievalLogger (F-06, F-09)
- `src/multimcp/retrieval/replay.py` — Recall@15, describe_rate gate fix (F-11)
- `src/multimcp/retrieval/metrics.py` — wire ALERT_RESCORE_RATE into check() (F-11)

**Success criteria (from source plan):**
1. YAML config controls `enabled`, `shadow_mode`, `scorer`, `max_k`, `canary_percentage`, `rollout_stage` (source plan lines 496-506, 963-971)
2. Default config: `enabled=False, shadow_mode=False` per source plan; operators enable via YAML without code changes (source plan lines 963-971)
3. `FileRetrievalLogger` writes JSONL when log path configured (source plan lines 942-955)
4. `Recall@15` computed in cutover gate checks (source plan line 975)
5. Describe-rate gate uses baseline comparison with >=20% drop requirement (source plan line 975)
6. `ALERT_RESCORE_RATE` checked in `AlertChecker.check()` (source plan lines 957-961)

---

## Execution Order

```
Phase 7 (Core Pipeline Wiring)
  → Phase 8 (Session State & Turn Boundary) [depends on 7]
    → Phase 9 (Rollout Activation) [depends on 8]
      → Re-plan Phase 5 (Post-GA Learning) against corrected codebase
        → Re-plan Phase 6 (Verification) against corrected codebase
```
