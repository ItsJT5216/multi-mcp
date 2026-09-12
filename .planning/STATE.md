---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 09
status: Executing Phase 09
stopped_at: "Completed 09-01-PLAN.md: YAML-driven retrieval config, E2E tests for V-01 through V-05"
last_updated: "2026-03-30T10:44:36.628Z"
progress:
  total_phases: 9
  completed_phases: 5
  total_plans: 20
  completed_plans: 13
---

# Project State

## Current Status

- **Milestone:** Phase 7+ — Core Pipeline Wiring + Hardening
- **Current Phase:** 09
- **Phase Status:** Integration sync — all review fixes applied, k8s removed, IP policing removed
- **Last Updated:** 2026-03-30

## Active Phase

**Phase 7: Core Pipeline Wiring — IN PROGRESS (1/1 plans complete)**

Goal: Wire all scoring and retrieval paths end-to-end so the BMXF pipeline actually executes per the source plan.

## Phase Progress

| Phase | Name | Status |
|-------|------|--------|
| 1 | Foundations | ✅ Complete |
| 2 | Safe Lexical MVP | ✅ Complete |
| 3 | Turn-by-Turn Adaptive | ✅ Complete (4/4 plans) |
| 4 | Rollout Hardening | ✅ Complete (4/4 plans) |
| 5 | Post-GA Learning | 📋 Planned (3 plans) |
| 6 | Verification & Compliance | 📋 Planned (1 plan) |
| 7 | Core Pipeline Wiring | ✅ Complete (1/1 plans) |
| 8 | Turn-Boundary State & Tool Call Rewrite | ✅ Complete |
| 9 | Replay Metrics & Rescore Monitoring | 🔄 In Progress |

## Session Continuity

Last session: 2026-03-30T10:37:18.858Z
Stopped at: Completed 09-01-PLAN.md: YAML-driven retrieval config, E2E tests for V-01 through V-05

## Context Notes

- Plan source: `docs/PHASE2-SYNTHESIZED-PLAN.md`
- Existing retrieval module at `src/multimcp/retrieval/` — 46+ tests must stay green
- BMX source to copy: `/home/tanner/MCPServer/src/meta_mcp/rag/retrieval/bmx.py`
- Tool namespace format: `server_name__tool_name` (double underscore via `_make_key()`)
- `MCPProxyServer.retrieval_pipeline` is currently TYPE_CHECKING-only import, `Optional[RetrievalPipeline] = None`
- New retrievers must implement `rebuild_index(registry: dict[str, ToolMapping])` pattern

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-28 | BMX over BM25/TF-IDF as primary scorer | +1.15 nDCG@10 on BEIR; self-tuning params; pure Python; no external deps |
| 2026-03-28 | BMXF field weighting (5 sub-indexes) | 5-10% accuracy on name/namespace — strongest retrieval signal in tool corpus |
| 2026-03-28 | Shadow mode mandatory before enabling | Safe migration; existing behavior preserved until metrics confirm improvement |
| 2026-03-28 | Conservative K=15-20 | Lean context window; routing tool as safety net for missed tools |
| 2026-03-29 | Re-export RootEvidence/WorkspaceEvidence from models.py | canonical source stays in models.py; evidence.py is a re-export shim |
| 2026-03-29 | Family cap applied against original total (not post-cap) | simpler, avoids iterative convergence; 35% of original total per family |
| 2026-03-29 | Confidence = min(1.0, unique_families/3) | 3 distinct signal families as heuristic for rich workspace context |
| 2026-03-29 | Lazy import ROUTING_TOOL_KEY in _call_tool() | Avoids circular import between mcp_proxy and retrieval subpackage |
| 2026-03-29 | RankingEvent.turn_number=0 for Phase 2 | Turn tracking deferred to Phase 3 |
| 2026-03-29 | Tier 6 fallback capped at 30 (not 20) | Wider visibility in zero-state sessions without full-registry exposure |
| 2026-03-29 | demote() slices safe_to_demote[:max_per_turn] in input order | Deterministic; preserves caller's priority ordering |
| 2026-03-29 | promote()/demote() return [] for unknown sessions | Consistent with add_tools() safe-default pattern; no exception raised |
| 2026-03-29 | Polyglot detection via config.max_k>17 heuristic | Conservative; full WorkspaceEvidence threading deferred; lets operators opt-in via config |
| 2026-03-29 | on_tool_called promotes tool_name if in registry (promote-on-call) | Immediate disclosure of called tool; simpler than RRF blend at call time |
| 2026-03-29 | SHA-256 over MD5 for canary bucket hashing (04-01) | Functionally equivalent for A/B assignment; avoids security scanner false positives on MD5 |
| 2026-03-29 | canary_percentage range 0.0-100.0 not 0.0-1.0 (04-01) | Human-readable operator config; plan spec |
| 2026-03-29 | Rollout guard: shadow->control, ga->canary, canary->hash-based (04-01) | Deterministic stage dispatch; safe default is shadow=control |
| 2026-03-29 | shadow mode (default) returns all tools — tests use rollout_stage=ga for filtering (04-03) | Shadow=passthrough is backward compatible; filtering tests must explicitly opt-in to ga mode |
| 2026-03-29 | is_filtered flag gates bounded active set vs passthrough in get_tools_for_list (04-03) | Single branch point for canary routing keeps logic readable |
| 2026-03-29 | FileRetrievalLogger.log_alert uses lazy import time as _time (04-03) | Avoids module-level name collision; consistent with existing patterns |
| 2026-03-29 | AlertChecker takes MetricSnapshot not RollingMetrics (04-04) | Separates computation from alerting; enables unit testing without time.monotonic() |
| 2026-03-29 | pct() uses min(int(p*n), n-1) index matching replay.py (04-04) | Consistent percentile calculation across offline replay and online RollingMetrics |
| 2026-03-30 | top_k default 10→15 per source plan line 496 (07-01) | Matches synthesized plan canonical value |
| 2026-03-30 | NAMESPACE_ALIASES uses exact server-name keys not substring fragments (07-01) | Source plan lines 584-597; 'gh' no longer matches 'github' |
| 2026-03-30 | BMXFRetriever dual indexes: env (alpha=0.5) + nl (alpha=None) (07-01) | Source plan lines 287, 297; query_mode selects index |
| 2026-03-30 | Routing dispatch uses ROUTING_TOOL_NAME not ROUTING_TOOL_KEY (07-01) | Model calls "request_tool" not "__routing__request_tool" |
| 2026-03-30 | dynamic_k evidence-based not config.max_k heuristic (07-01) | Source plan line 292; 15 base, 18 polyglot, cap 20 |
| 2026-03-30 | Tier 6 caps at 12 tools (not 30); anchor seeding replaced by fallback ladder (07-01) | Source plan line 898; anchor concept replaced by scoring-based active set |
| 2026-03-30 | **TEMP** Default port changed 8085 → 8083 for Phase 8/9 test runs | User's always-on multi-mcp instance occupies 8085; conflicts with e2e test_sse_mode. **Revert to 8085 after Phase 9**: tests/e2e_test.py:38, main.py:17, src/multimcp/multi_mcp.py:34, start-server.sh |
