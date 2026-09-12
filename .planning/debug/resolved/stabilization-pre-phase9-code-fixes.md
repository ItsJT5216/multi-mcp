---
status: resolved
trigger: "Investigate and fix two blocking issues: router-proxy double-accounting and Phase 9 rollout drift."
created: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:01:00Z
---

## Current Focus

hypothesis: Both bugs are confirmed — double-accounting in _call_tool and premature GA in multi_mcp.py
test: Applying surgical fixes to both files
expecting: proxied calls → proxy only; multi_mcp starts in shadow mode
next_action: Apply Fix A (suppress direct on_tool_called inside proxy path) and Fix B (shadow_mode=True, rollout_stage="shadow" + guard order)

## Symptoms

expected: Proxied tool invocations counted as proxy only; multi_mcp.py starts in coherent shadow mode
actual: Proxied calls double-counted (both direct and proxy); multi_mcp.py hardcodes GA mode
errors: Semantic correctness bugs — no crash, but RankingEvent.direct_tool_calls / router_proxies are contaminated
reproduction: Any proxied routing-tool execution via _call_tool() in mcp_proxy.py
started: Introduced during branch merge of Phase 8 + partial Phase 9 leak

## Eliminated

(none — symptoms were pre-confirmed)

## Evidence

- timestamp: 2026-03-30T00:00:00Z
  checked: mcp_proxy.py _call_tool() lines 467-602
  found: When routing tool returns __PROXY_CALL__, line 498 calls self._call_tool(proxy_req) recursively. The inner call reaches the normal tool path (lines 569-600) which calls on_tool_called(...) without is_router_proxy=True (line 596). Then the outer proxy path (lines 499-514) calls on_tool_called(..., is_router_proxy=True). Both fire for the same tool invocation.
  implication: Fix A — inner recursive call must skip the on_tool_called() recording; only the outer proxy path records.

- timestamp: 2026-03-30T00:00:00Z
  checked: multi_mcp.py lines 546-549
  found: retrieval_config = RetrievalConfig(enabled=True, shadow_mode=False, rollout_stage="ga") — hardcodes GA mode prematurely.
  implication: Fix B — change to shadow_mode=True, rollout_stage="shadow".

- timestamp: 2026-03-30T00:00:00Z
  checked: pipeline.py get_tools_for_list() lines 504-507
  found: is_filtered = (rollout_stage == "ga" or (rollout_stage == "canary" and group == "canary")). With shadow_mode=False and rollout_stage="ga", is_filtered=True and the bounded active set is returned. The shadow_mode check exists in the docstring but is NOT a first-pass guard — is_filtered is computed from rollout_stage only, then shadow_mode path is the else branch at line 784.
  implication: Fix B also needs to ensure shadow_mode=True takes precedence as the FIRST dispatch guard in get_tools_for_list().

## Resolution

root_cause: (A) _call_tool() recursive inner call fires on_tool_called() as a direct call before the outer proxy path fires on_tool_called(is_router_proxy=True), causing double-counting in RankingEvent.direct_tool_calls and router_proxies. (B) multi_mcp.py hardcoded rollout_stage="ga"/shadow_mode=False prematurely — pipeline.get_tools_for_list() had no first-pass shadow_mode guard, so the bounded active set was returned instead of all tools.
fix: (A) Added _skip_pipeline_record=False kwarg to _call_tool(). Routing-tool proxy branch passes _skip_pipeline_record=True when making the recursive inner call, suppressing the normal on_tool_called() write. Only the outer proxy path records (is_router_proxy=True). (B) Changed RetrievalConfig to shadow_mode=True/rollout_stage="shadow" in multi_mcp.py. Added explicit shadow_mode first-pass guard in pipeline.get_tools_for_list() — if shadow_mode=True: is_filtered=False before any rollout_stage logic.
verification: All 1109 tests pass (uv run pytest tests/ -x -q, excluding e2e and lifecycle).
files_changed: [src/multimcp/mcp_proxy.py, src/multimcp/retrieval/pipeline.py, src/multimcp/multi_mcp.py]
