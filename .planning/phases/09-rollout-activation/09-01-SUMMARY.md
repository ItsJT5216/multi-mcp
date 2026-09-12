---
phase: 09-rollout-activation
plan: 01
subsystem: retrieval
tags: [yaml-config, rollout, shadow-mode, canary, bmxf, retrieval, metrics, observability]

# Dependency graph
requires:
  - phase: 08-session-turn-boundary
    provides: transport-derived session IDs for canary routing
  - phase: 07-core-pipeline-wiring
    provides: weighted_rrf/compute_alpha wired, ROUTING_TOOL_NAME dispatch fixed
  - phase: 04-rollout-hardening
    provides: RollingMetrics, AlertChecker, replay.py cutover gates
provides:
  - YAML-driven RetrievalConfig replacing hardcoded shadow bootstrap
  - RetrievalSettings with all Phase 2+4 fields in yaml_config.py
  - Backward compat: no retrieval: block → enabled=False → all tools returned
  - E2E tests replacing V-01 through V-05 overstated claims
  - 09-VERIFICATION.md with V-01–V-06 correction references
affects: [rollout-operators, phase-10-if-any, post-ga-learning]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "YAML-driven config: all rollout state flows from yaml_config.retrieval, not hardcoded"
    - "Logger selection: log_path empty -> NullLogger, log_path set -> FileRetrievalLogger"
    - "RollingMetrics gated on enabled: only created when retrieval_config.enabled=True"

key-files:
  created:
    - tests/test_e2e_routing_dispatch.py
    - tests/test_e2e_rrf_called.py
    - tests/test_e2e_production_config.py
    - tests/test_e2e_session_isolation.py
  modified:
    - src/multimcp/yaml_config.py
    - src/multimcp/multi_mcp.py
    - tests/test_rollout_runtime_modes.py
    - tests/test_retrieval_config.py

key-decisions:
  - "YAML-driven default: RetrievalSettings() defaults give enabled=False (safe no-op) — operators must explicitly opt in to retrieval"
  - "Logger selection from yaml_retrieval.log_path: empty string -> NullLogger, non-empty -> FileRetrievalLogger"
  - "RollingMetrics created only when enabled=True: avoids memory overhead for disabled pipelines"
  - "E2E tests are behavioral, not structural: patch-based runtime invocation verification replaces grep-based import checks"
  - "top_k default corrected from 10 to 15 in RetrievalSettings (matches source plan line 496)"

patterns-established:
  - "Config-driven pipeline: yaml_config.retrieval is sole source of truth for RetrievalConfig"
  - "No hardcoded rollout state in application bootstrap after Phase 9"

requirements-completed: [WIRE-01, WIRE-02, CATALOG-04]

# Metrics
duration: 8min
completed: 2026-03-30
---

# Phase 9 Plan 01: Rollout Activation & Observability Summary

**YAML-driven RetrievalConfig wired to replace hardcoded shadow bootstrap; E2E tests added for V-01 through V-05 gap closure; all 1145 tests passing**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-30T10:28:23Z
- **Completed:** 2026-03-30T10:35:43Z
- **Tasks:** 4 (yaml_config.py + multi_mcp.py + E2E tests + verification artifacts)
- **Files modified:** 9

## Accomplishments

- Removed `_make_startup_retrieval_config()` — the hardcoded shadow bootstrap that forced `enabled=True, shadow_mode=True` at every startup regardless of YAML config
- Expanded `RetrievalSettings` in `yaml_config.py` with all Phase 2+4 fields: shadow_mode, scorer, max_k, enable_routing_tool, enable_telemetry, telemetry_poll_interval, canary_percentage, rollout_stage, log_path
- Built YAML-driven `RetrievalConfig` from `yaml_config.retrieval` in `multi_mcp.py`; logger selection from log_path; `RollingMetrics` gated on enabled flag
- Added 4 new E2E test files replacing V-01 through V-05 overstated verification claims with behavioral runtime tests
- Added Phase 9 mandatory test suite (off/shadow/canary/ga mode, YAML fields, backward compat, logger selection)
- All 1145 tests pass after fixing `test_retrieval_config.py` top_k assertion from 10 to 15

## Task Commits

Each task was committed atomically:

1. **Task 1: yaml_config.py RetrievalSettings expansion** - `d393c11` (feat)
2. **Task 2: multi_mcp.py YAML-driven init + test_rollout_runtime_modes.py Phase 9 mandatory tests** - `ebadb6e` (feat)
3. **Task 3: E2E replacement tests for V-01 through V-05 + test_retrieval_config.py fix** - `ba260d6` (feat)

## Files Created/Modified

- `src/multimcp/yaml_config.py` - Added 11 new fields to RetrievalSettings; corrected top_k default from 10 to 15
- `src/multimcp/multi_mcp.py` - Removed `_make_startup_retrieval_config()`; replaced pipeline init with YAML-driven RetrievalConfig; YAML log_path-based logger selection; rolling_metrics gated on enabled
- `tests/test_rollout_runtime_modes.py` - Replaced startup-wiring tests with Phase 9 mandatory test contract (off/shadow/canary/ga modes, YAML fields, backward compat, logger selection)
- `tests/test_e2e_routing_dispatch.py` - E2E test: request_tool reaches handle_routing_call() (replaces V-02 grep)
- `tests/test_e2e_rrf_called.py` - E2E test: weighted_rrf() called at runtime turn>0 (replaces V-01/V-03)
- `tests/test_e2e_production_config.py` - E2E test: enabled read from YAML not hardcoded (replaces V-04)
- `tests/test_e2e_session_isolation.py` - E2E test: distinct session IDs isolate pipeline state (replaces V-05)
- `tests/test_retrieval_config.py` - Fixed top_k assertion from 10 to 15 (matches source plan)

## Decisions Made

- YAML-driven default: `RetrievalSettings()` gives `enabled=False` — operators must explicitly configure retrieval in YAML; backward compat preserved
- Logger selection based on `yaml_retrieval.log_path`: empty string → NullLogger (safe no-op), non-empty → FileRetrievalLogger
- `RollingMetrics` only instantiated when `retrieval_config.enabled=True` — avoids memory overhead for disabled pipelines
- top_k default corrected to 15 matching source plan line 496 (was 10 in Phase 2 pre-plan value)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_retrieval_config.py top_k assertion**
- **Found during:** Task 3 (running full test suite)
- **Issue:** `test_retrieval_config.py::TestRetrievalSettings::test_defaults_disabled` expected `top_k == 10` but the plan explicitly changes the default to 15 (source plan line 496)
- **Fix:** Updated assertion to `top_k == 15` with comment explaining the change
- **Files modified:** `tests/test_retrieval_config.py`
- **Verification:** `uv run pytest tests/test_retrieval_config.py -q` passes
- **Committed in:** `ba260d6`

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug: pre-existing test expected old default)
**Impact on plan:** Required for correctness — the test was validating the old value that the plan explicitly changes.

## Issues Encountered

- `WorkspaceEvidence` dataclass does not have a `partial_scan` field (it exists on `RootEvidence`). Fixed in E2E test during writing.
- `handle_routing_call()` is synchronous, not async. Updated test assertion to use `callable()` not `asyncio.iscoroutinefunction()`.
- `mcp_proxy.py` dispatches on `ROUTING_TOOL_NAME` not `ROUTING_TOOL_KEY`. Updated structural test to check the correct constant.

## Sections Verified as Already Landed

Per the plan, the following sections were verified as complete and required no code changes:

- **Section 3 (replay.py):** All Phase 9 fields (`recall_at_15`, `canary_recall`, `control_recall`, `canary_describe_rate`, `control_describe_rate`) present; `_compute_group_recall()`, `_compute_describe_rate()`, `check_cutover_gates()` all correct
- **Section 4 (Recall@15 gate):** Shadow exclusion, ≥20-event guard, router_proxies counted as recall events — all verified via existing `test_replay_cutover_gates.py`
- **Section 5 (describe-rate gate):** Relative drop formula, shadow exclusion, ≥20-event guard — all verified via existing `test_replay_cutover_gates.py`
- **Section 6 (metrics.py + pipeline.py):** `record_rescore()`, `rescore_rate_10m`, `AlertChecker` rescore-rate check, `rolling_metrics` constructor param — all present
- **Section 7 (pipeline runtime semantics):** Shadow guard as first dispatch branch, canary via `get_session_group(session_id, config)`, GA all-sessions — verified via pipeline.py read
- **Section 8 (verification docs):** 02/03/04-VERIFICATION.md already have supersession notices and inline V-XX CORRECTED annotations; 09-VERIFICATION.md already exists

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All Phase 9 gap closures are complete (F-06, F-09, F-11 closed)
- Operators can enable retrieval by adding `retrieval: enabled: true` to YAML config
- Shadow → Canary → GA progression is fully config-driven, no code changes needed
- All 1145 tests pass; rollout infrastructure is production-ready

---
*Phase: 09-rollout-activation*
*Completed: 2026-03-30*
