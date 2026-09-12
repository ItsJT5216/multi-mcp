# Multi-MCP Phase 2: Versioned Roots-Anchored BMXF Routing

## What This Is

A code-grounded implementation of roots-anchored BMXF tool retrieval for the Multi-MCP proxy server. The system replaces the current `PassthroughRetriever` (returns all tools) with a context-aware retrieval pipeline that:

1. At session init, scans declared MCP roots to fingerprint the workspace
2. Uses BMX (entropy-weighted BM25 successor) with field weighting (BMXF) to score and rank tools
3. Exposes a bounded active set (K=15–20) with remaining tools accessible via a routing tool
4. Adapts turn-by-turn using blended RRF fusion of environment and conversation signals

## Core Value

**Turn-zero tool relevance without full-catalog exposure.** Every session starts with the right tools visible, not all 168 tools dumped into the model's context window.

## Context

- **Codebase:** `/home/tanner/Projects/multi-mcp`
- **Existing retrieval module:** `src/multimcp/retrieval/` with `KeywordRetriever`, `PassthroughRetriever`, `RelevanceRanker`, `TieredAssembler`, `SessionStateManager`, `RetrievalPipeline`
- **BMX source:** `/home/tanner/MCPServer/src/meta_mcp/rag/retrieval/bmx.py` — entropy-weighted BM25 successor (arXiv:2408.06643), pure Python, ~400 lines
- **Plan source:** `docs/PHASE2-SYNTHESIZED-PLAN.md` — fully synthesized and validated against live codebase
- **Tool count:** 168 tools now, ceiling 500
- **Scoring:** BMX with BMXF field weighting (`tool_name:3.0`, `namespace:2.5`, `retrieval_aliases:1.5`, `description:1.0`, `parameter_names:0.5`)

## Architecture

### New Files
- `src/multimcp/retrieval/bmx_index.py` — BMXIndex copied from bmx.py + BMXF field wrapper
- `src/multimcp/retrieval/bmx_retriever.py` — BMXFRetriever(ToolRetriever)
- `src/multimcp/retrieval/catalog.py` — ToolCatalogSnapshot, ToolDoc, build_snapshot()
- `src/multimcp/retrieval/routing_tool.py` — Synthetic MCP routing tool
- `src/multimcp/retrieval/fusion.py` — Weighted RRF, alpha-decay blend
- `src/multimcp/retrieval/telemetry/` — Root scanner, evidence, tokens, monitor

### Updated Files
- `src/multimcp/retrieval/models.py` — New dataclasses (ToolDoc, ToolCatalogSnapshot, SessionRoutingState, RankingEvent, updated RetrievalConfig)
- `src/multimcp/retrieval/session.py` — Promote/demote hysteresis (replaces monotonic guarantee)
- `src/multimcp/retrieval/assembler.py` — Routing-tool tier
- `src/multimcp/retrieval/pipeline.py` — Wire BMXF, fallback chain, RRF blend
- `src/multimcp/retrieval/logging.py` — FileRetrievalLogger
- `src/multimcp/mcp_proxy.py` — Wire retrieval_pipeline, register routing tool
- `src/multimcp/multi_mcp.py` — Instantiate pipeline with config

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| BMX over existing TF-IDF | +1.15 nDCG@10 on BEIR; self-tuning params; no manual k1/b tuning | Accepted |
| BMXF field weighting | 5-10% accuracy improvement on name/namespace matches | Accepted |
| Conservative K (15-20) | Lean context window; routing tool is safety net | Accepted |
| Six-stage fallback ladder | Never exposes full catalog; max directly exposed = 20 | Accepted |
| Shadow rollout mandatory | Safe migration via `shadow_mode` flag | Accepted |
| Telemetry restricted to roots | MCP spec compliance; privacy preservation | Accepted |
| Routing tool for demoted tools | All tools discoverable without full exposure | Accepted |

## Requirements

### Validated

- ✓ Multi-MCP proxy server (STDIO + SSE transport) — existing
- ✓ Tool namespacing (`server_name__tool_name`) — existing
- ✓ `RetrievalPipeline`, `ToolRetriever` ABC, `PassthroughRetriever` — existing
- ✓ `KeywordRetriever` (TF-IDF) — existing baseline retriever
- ✓ `RetrievalConfig` with `enabled` kill switch — existing
- ✓ `SessionStateManager` — existing session management
- ✓ `RetrievalLogger` ABC, `NullLogger` — existing observability hooks
- ✓ MCP roots telemetry scanning (allowlisted, bounded) — Validated in Phase 2: Safe Lexical MVP
- ✓ Bounded fallback ladder (6 tiers, max exposed = 20) — Validated in Phase 2: Safe Lexical MVP
- ✓ Routing tool registered as synthetic MCP tool — Validated in Phase 2: Safe Lexical MVP
- ✓ `FileRetrievalLogger` implementing `RetrievalLogger` ABC — Validated in Phase 2: Safe Lexical MVP

### Validated (Phase 9 complete — all requirements delivered)

- ✓ BMXF retriever implementing `ToolRetriever` ABC — Validated in Phase 2: Safe Lexical MVP
- ✓ `ToolCatalogSnapshot` with versioning and `schema_hash` — Validated in Phase 2: Safe Lexical MVP
- ✓ Turn-by-turn RRF fusion with alpha-decay — Validated in Phase 7: Core Pipeline Wiring
- ✓ Promote/demote hysteresis (replaces monotonic guarantee) — Validated in Phase 3: Turn-by-Turn Adaptive
- ✓ Shadow mode and feature flags in `RetrievalConfig` — Validated in Phase 9: Rollout Activation
- ✓ Test suite parallel to existing naming conventions — Validated across Phases 2–9
- ✓ Canary rollout infrastructure (canary_percentage, rollout_stage, is_canary_session) — Validated in Phase 4: rollout-hardening
- ✓ Offline replay evaluator with cutover gates (p95 < 50ms, tier56 < 5%) — Validated in Phase 4: rollout-hardening
- ✓ Online rolling metrics (RollingMetrics, AlertChecker) — Validated in Phase 4: rollout-hardening
- ✓ Operator runbook for shadow → canary → GA lifecycle — Validated in Phase 4: rollout-hardening

### Out of Scope

- Neural retrieval / embeddings — Phase 4 (deferred, needs tool count >500)
- TOON micro-descriptions — Post-GA
- PPMI token reweighting — Phase 4 (needs >50 sessions of data)
- Co-occurrence graph — Phase 4
- Process/env-var inspection — Privacy boundary (explicitly excluded)
- GPU usage — CPU-only by design

## Current State

**Phase 9 complete (2026-03-30)** — All 9 phases delivered. YAML-driven rollout activation complete. No hardcoded shadow bootstrap remains. Operators enable retrieval via `retrieval: enabled: true` in YAML config. Shadow → Canary → GA progression is fully config-driven. 1148 tests pass.

## Evolution

This document evolves at phase transitions and milestone boundaries.

Last updated: 2026-03-30

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

---
*Last updated: 2026-03-30 — Phase 9 complete (rollout-activation)*
