---
status: resolved
trigger: "proxy-direct-accounting-semantics: RankingEvent.direct_tool_calls contaminated by proxy calls"
created: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:01:00Z
---

## Current Focus

hypothesis: on_tool_called() always appends to _session_tool_history regardless of is_router_proxy flag; RankingEvent.direct_tool_calls reads from _session_tool_history; this causes proxy calls to appear in BOTH direct_tool_calls AND router_proxies
test: read pipeline.py on_tool_called() and the RankingEvent construction code in get_tools_for_list() step 12
expecting: confirm _session_tool_history is written unconditionally, and direct_tool_calls is sourced from it
next_action: fix by adding _direct_tool_calls accumulator; wire direct_tool_calls to that instead of _session_tool_history

## Symptoms

expected: |
  - true direct tool calls appear in RankingEvent.direct_tool_calls ONLY
  - router-proxied tool calls appear in RankingEvent.router_proxies ONLY
  - describe-only routing calls appear in RankingEvent.router_describes ONLY
  - no proxied call appears in direct_tool_calls
  - replay and Tier 5 semantics do not double-count proxy-used tools

actual: |
  - _session_tool_history is used for conversation context
  - on_tool_called(..., is_router_proxy=True) still appends to _session_tool_history
  - RankingEvent.direct_tool_calls is populated from _session_tool_history (which includes proxy calls)
  - router_proxies is ALSO populated separately
  - Result: proxied tool usage appears in BOTH direct_tool_calls AND router_proxies

errors: "No runtime error — this is a semantic correctness issue. The data is silently wrong."

reproduction: |
  1. Make a proxy (router) tool call via MCPProxyServer._call_tool() with is_router_proxy=True
  2. Check the resulting RankingEvent
  3. The proxied tool name appears in BOTH direct_tool_calls and router_proxies

timeline: "Introduced when _session_tool_history was unified for both conversation context and direct-call ledger purposes"

## Eliminated

(none yet)

## Evidence

- timestamp: 2026-03-30T00:00:00Z
  checked: pipeline.py on_tool_called() lines 840-841
  found: |
    hist = self._session_tool_history.setdefault(session_id, [])
    hist.append(tool_name)

    This runs BEFORE the is_router_proxy check (line 847). So proxy calls are unconditionally appended to _session_tool_history.
  implication: Every call (direct OR proxy) enters _session_tool_history.

- timestamp: 2026-03-30T00:00:00Z
  checked: pipeline.py get_tools_for_list() step 12 (lines 732-752)
  found: |
    session_direct_calls = list(self._session_tool_history.get(session_id, []))
    ...
    event = RankingEvent(
        ...
        direct_tool_calls=session_direct_calls,  # sourced from _session_tool_history!
        router_proxies=session_router_proxies,    # sourced from _session_router_proxies
        ...
    )

    _session_tool_history contains BOTH direct calls AND proxy calls.
    _session_router_proxies contains ONLY proxy calls.
    So proxied tools appear in BOTH direct_tool_calls AND router_proxies.
  implication: This is the exact bug. direct_tool_calls is semantically dirty.

- timestamp: 2026-03-30T00:00:00Z
  checked: existing test_router_accounting.py test_tier5_fields_correct_separation (lines 147-181)
  found: |
    The test asserts "server__00_tool" and "server__01_tool" in event.direct_tool_calls
    (lines 168-169) but has a comment: "(Note: on_tool_called always writes to
    _session_tool_history regardless of is_router_proxy)". The test does NOT assert
    "server__06_tool" NOT in event.direct_tool_calls — so it doesn't catch the bug.
  implication: Existing test coverage is incomplete; no test currently asserts the negative case.

- timestamp: 2026-03-30T00:00:00Z
  checked: mcp_proxy.py _call_tool() routing proxy path (lines 509-528)
  found: |
    The proxy path calls self._call_tool(proxy_req, _skip_pipeline_record=True) for the
    inner call, then calls on_tool_called(..., is_router_proxy=True) once. So the inner call
    does NOT double-call on_tool_called. But on_tool_called with is_router_proxy=True still
    writes to _session_tool_history (lines 840-841 in pipeline.py).
  implication: _skip_pipeline_record fix is intact. The bug is solely in on_tool_called()
    writing to _session_tool_history unconditionally.

## Resolution

root_cause: |
  In pipeline.py on_tool_called(), lines 840-841 unconditionally append tool_name to
  _session_tool_history regardless of is_router_proxy. Step 12 of get_tools_for_list()
  reads direct_tool_calls from _session_tool_history, so proxy calls contaminate it.

  Fix: Add _direct_tool_calls accumulator. Only write to it when is_router_proxy=False.
  Source direct_tool_calls from _direct_tool_calls instead of _session_tool_history.
  Keep _session_tool_history for conversation context (unchanged).

fix: |
  1. Add self._direct_tool_calls: dict[str, list[str]] = {} to __init__
  2. In on_tool_called(): only append to _direct_tool_calls when NOT is_router_proxy
  3. In get_tools_for_list() step 12: source direct_tool_calls from _direct_tool_calls
  4. In cleanup_session(): pop _direct_tool_calls

verification: |
  - Ran uv run pytest tests/test_router_accounting.py -v: 11 passed
  - Ran uv run pytest tests/test_replay_cutover_gates.py tests/test_fallback_ladder.py -v: 36 passed
  - Ran uv run pytest tests/ (excluding e2e/lifecycle): 1122 passed, 0 failures
  - New tests prove: proxy NOT in direct, direct NOT in proxy, describe NOT in either,
    no double-counting on same tool called both ways.
  - _skip_pipeline_record fix confirmed intact (test_proxy_routing_call_not_recorded_as_direct passes)
files_changed: [src/multimcp/retrieval/pipeline.py, tests/test_router_accounting.py]
