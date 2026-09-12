---
status: resolved
trigger: "Add and update tests that verify the stabilization fixes for router-proxy accounting and rollout shadow mode."
created: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:00:00Z
---

## Current Focus

hypothesis: Test coverage gaps exist for (a) proxy routing path in MCPProxyServer._call_tool(), and (b) shadow_mode/rollout_stage as a dispatch guard
test: Write two new test files targeting runtime truth
expecting: Tests pass with pytest -s when production code is correct; tests expose divergence when it is not
next_action: Write tests/test_router_accounting.py additions and tests/test_rollout_runtime_modes.py

## Symptoms

expected: Tests verify proxy/direct/describe separation and shadow_mode as first dispatch guard
actual: No tests cover the real MCPProxyServer._call_tool() routing proxy path; no test for shadow_mode guard ordering
errors: Missing test coverage for runtime correctness
reproduction: N/A — test gap
started: Phase 8 completed without full accounting coverage

## Eliminated

- hypothesis: Existing test_router_accounting.py covers proxy path via MCPProxyServer
  evidence: File tests RetrievalPipeline internals only (record_router_describe, on_tool_called); no MCPProxyServer._call_tool() proxy path test
  timestamp: 2026-03-30

- hypothesis: shadow_mode field in RetrievalConfig is checked by pipeline.get_tools_for_list()
  evidence: pipeline.py uses rollout_stage to compute is_filtered; shadow_mode is only used by BMXFRetriever.retrieve() for retriever-level scoring passthrough
  timestamp: 2026-03-30

## Evidence

- timestamp: 2026-03-30
  checked: mcp_proxy.py _call_tool() routing tool dispatch path (lines 467-520)
  found: When routing tool returns __PROXY_CALL__:{name}, proxy_result is fetched and on_tool_called(..., is_router_proxy=True) is called on retrieval_pipeline
  implication: MCPProxyServer._call_tool() is the runtime truth for proxy accounting; pipeline tests only test internal state, not the proxy dispatch path

- timestamp: 2026-03-30
  checked: pipeline.py get_tools_for_list() is_filtered computation (lines 505-508)
  found: is_filtered = (rollout_stage == "ga") or (rollout_stage == "canary" and group == "canary"); shadow_mode field is NOT consulted here
  implication: "shadow mode" at pipeline level means rollout_stage="shadow" → is_filtered=False → all tools returned

- timestamp: 2026-03-30
  checked: multi_mcp.py startup wiring (lines 546-549)
  found: retrieval_config = RetrievalConfig(enabled=True, shadow_mode=False, rollout_stage="ga")
  implication: Current startup wires GA mode (filtering active), not shadow mode; test 4 requirement (shadow wiring) will fail against current production code

- timestamp: 2026-03-30
  checked: bmx_retriever.py shadow_mode usage (lines 210, 242)
  found: shadow_mode=True in BMXFRetriever makes retrieve() return all candidates with scores (no truncation); this is the retriever-level shadow, separate from pipeline-level shadow
  implication: Test 3 (shadow_mode as dispatch guard) must target pipeline rollout_stage="shadow" behavior, not BMXFRetriever shadow_mode

## Resolution

root_cause: Test coverage gaps in (1) MCPProxyServer._call_tool() proxy accounting path and (2) pipeline rollout_stage shadow dispatch guard and startup wiring
fix: Added 3 tests to tests/test_router_accounting.py covering MCPProxyServer._call_tool() proxy dispatch runtime path; created tests/test_rollout_runtime_modes.py with 6 tests covering shadow mode dispatch guard and startup wiring coherence
verification: All 13 tests pass: uv run pytest tests/test_router_accounting.py tests/test_rollout_runtime_modes.py → 13 passed in 0.18s
files_changed: [tests/test_router_accounting.py, tests/test_rollout_runtime_modes.py]
