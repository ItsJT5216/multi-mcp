# GSD Debug Knowledge Base

Resolved debug sessions. Used by `gsd-debugger` to surface known-pattern hypotheses at the start of new investigations.

---

## fix-test-failures-phase08 — 4 pre-existing test failures: weather tool missing, ProcessLookupError, kind not found
- **Date:** 2026-03-30
- **Error patterns:** weather__get_weather, AssertionError, ProcessLookupError, kind, _PRIVATE_RANGES, NotImplementedError, MultiServerMCPClient, langchain-mcp-adapters, SSRF, localhost, ConnectError, _validate_url
- **Root cause:** (1) multi_mcp.py run() always called _bootstrap_from_yaml() regardless of --config, loading 100+ servers so weather was never returned by retrieval; (2) _validate_url SSRF check was in _create_single_client() blocking localhost static configs; (3) missing "type":"sse" caused wrong transport auto-detection; (4) langchain-mcp-adapters 0.2.2 removed context manager + connect_to_server API; (5) _PRIVATE_RANGES removed from mcp_client.py breaking import; (6) k8s manifest port 8080 vs app port 8083 mismatch
- **Fix:** Gate _bootstrap_from_yaml on not self.settings.config; add _build_config_from_json_file(); move SSRF check to POST handler only; add "type":"sse" to mcp_sse.json; add conftest.py with MultiServerMCPClient compatibility shim; restore _PRIVATE_RANGES (fe80::/10, NOT loopback); fix k8s manifest to port 8083; add msc/mcp.json
- **Files changed:** src/multimcp/multi_mcp.py, src/multimcp/mcp_client.py, examples/config/mcp_sse.json, examples/k8s/multi-mcp.yaml, conftest.py, msc/mcp.json
---

## stabilization-pre-phase9-tests — test coverage gaps for proxy accounting path and rollout runtime modes
- **Date:** 2026-03-30
- **Error patterns:** test_router_accounting, test_rollout_runtime_modes, MCPProxyServer, _call_tool, proxy path, shadow_mode, rollout_stage, is_filtered, dispatch guard, startup wiring, on_tool_called, is_router_proxy, coverage gap
- **Root cause:** Test coverage gaps in (1) MCPProxyServer._call_tool() proxy accounting path — existing tests only exercised RetrievalPipeline internals, not the full proxy dispatch path — and (2) pipeline rollout_stage shadow dispatch guard and startup wiring coherence, with no tests verifying shadow_mode/rollout_stage as a first-pass guard.
- **Fix:** Added 3 tests to tests/test_router_accounting.py covering MCPProxyServer._call_tool() proxy dispatch runtime path; created tests/test_rollout_runtime_modes.py with 6 tests covering shadow mode dispatch guard and startup wiring coherence. All 13 tests pass.
- **Files changed:** tests/test_router_accounting.py, tests/test_rollout_runtime_modes.py
---

## proxy-direct-accounting-semantics — proxied tool calls contaminating RankingEvent.direct_tool_calls
- **Date:** 2026-03-30
- **Error patterns:** direct_tool_calls, router_proxies, RankingEvent, _session_tool_history, on_tool_called, is_router_proxy, double-counting, Tier 5, frequency prior, replay, Recall@15, contaminate
- **Root cause:** on_tool_called() unconditionally appended tool_name to _session_tool_history regardless of is_router_proxy flag. get_tools_for_list() step 12 sourced RankingEvent.direct_tool_calls from _session_tool_history, which contained both direct and proxy calls. Proxy calls therefore appeared in BOTH direct_tool_calls AND router_proxies simultaneously.
- **Fix:** Added _direct_tool_calls dict to RetrievalPipeline. In on_tool_called(), only append to _direct_tool_calls when is_router_proxy=False. Changed step 12 to source direct_tool_calls from _direct_tool_calls instead of _session_tool_history. _session_tool_history continues to receive all calls for conversation context. Added cleanup_session() pop for _direct_tool_calls.
- **Files changed:** src/multimcp/retrieval/pipeline.py, tests/test_router_accounting.py
---

## stabilization-pre-phase9-code-fixes — router-proxy double-accounting and premature GA rollout
- **Date:** 2026-03-30
- **Error patterns:** on_tool_called, is_router_proxy, direct_tool_calls, router_proxies, RankingEvent, __PROXY_CALL__, _call_tool, shadow_mode, rollout_stage, ga, is_filtered, double-counting, double-counted
- **Root cause:** (A) _call_tool() recursive inner call for routing-tool proxy invocations fired on_tool_called() as a direct call, then the outer proxy path also fired on_tool_called(is_router_proxy=True), double-counting both direct_tool_calls and router_proxies in RankingEvent. (B) multi_mcp.py hardcoded rollout_stage="ga"/shadow_mode=False prematurely; pipeline.get_tools_for_list() had no first-pass shadow_mode guard so the bounded active set was returned instead of all tools.
- **Fix:** (A) Added _skip_pipeline_record=False kwarg to _call_tool(); routing-tool proxy branch passes _skip_pipeline_record=True on the recursive inner call, suppressing the direct on_tool_called() write. (B) Changed RetrievalConfig to shadow_mode=True/rollout_stage="shadow" in multi_mcp.py; added shadow_mode as the first-pass guard in get_tools_for_list() (is_filtered=False when shadow_mode=True, before any rollout_stage logic).
- **Files changed:** src/multimcp/mcp_proxy.py, src/multimcp/retrieval/pipeline.py, src/multimcp/multi_mcp.py
---

