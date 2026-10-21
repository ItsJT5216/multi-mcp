"""Tests for RootMonitor — adaptive polling and significance-based re-score triggering.

Tests: TELEM-05 (adaptive polling, significance threshold, debounce).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.multimcp.retrieval.telemetry.monitor import RootMonitor, _POLL_SCHEDULE, _SIGNIFICANCE_THRESHOLD


# ── Basic instantiation ───────────────────────────────────────────────────────

def test_root_monitor_instantiable_without_scanner():
    """RootMonitor(scanner=None) is valid — no import errors, no crash."""
    m = RootMonitor()
    assert m is not None


def test_root_monitor_initial_poll_interval():
    """Initial poll interval must be 5.0 seconds (first entry in schedule)."""
    m = RootMonitor()
    assert m.poll_interval == 5.0


def test_poll_schedule_values():
    """Adaptive poll schedule must be [5, 10, 20, 30]."""
    assert _POLL_SCHEDULE == [5.0, 10.0, 20.0, 30.0]


def test_significance_threshold_default():
    """Default significance threshold must be 0.7."""
    assert _SIGNIFICANCE_THRESHOLD == 0.7


# ── check_for_changes with no recorded changes ────────────────────────────────

def test_check_for_changes_returns_false_initially():
    """check_for_changes() returns False before any record_change calls."""
    m = RootMonitor()
    assert m.check_for_changes() is False


def test_check_for_changes_returns_false_below_threshold():
    """check_for_changes() returns False when cumulative significance < 0.7."""
    m = RootMonitor()
    m.record_change(0.3)
    m.record_change(0.3)
    # 0.3 + 0.3 = 0.6 — below threshold
    assert m.check_for_changes() is False


# ── check_for_changes with threshold met ─────────────────────────────────────

def test_check_for_changes_returns_true_at_threshold():
    """check_for_changes() returns True when cumulative significance >= 0.7."""
    m = RootMonitor(min_debounce_s=0.0)  # disable debounce for test
    m.record_change(0.8)
    assert m.check_for_changes() is True


def test_check_for_changes_cumulative():
    """record_change accumulates: 0.4 + 0.4 = 0.8 triggers True."""
    m = RootMonitor(min_debounce_s=0.0)
    m.record_change(0.4)
    m.record_change(0.4)
    assert m.check_for_changes() is True


def test_check_for_changes_at_exact_threshold():
    """Exactly 0.7 cumulative significance triggers True."""
    m = RootMonitor(min_debounce_s=0.0)
    m.record_change(0.7)
    assert m.check_for_changes() is True


# ── Debounce mechanism ────────────────────────────────────────────────────────

def test_debounce_prevents_back_to_back_trigger():
    """After acknowledge(), check_for_changes() returns False within debounce window."""
    m = RootMonitor(min_debounce_s=10.0)
    m.record_change(0.8)
    assert m.check_for_changes() is True  # triggers
    m.acknowledge()
    m.record_change(0.8)  # new accumulation
    # Still within debounce window
    assert m.check_for_changes() is False


def test_debounce_zero_allows_immediate_re_trigger():
    """With min_debounce_s=0.0, can trigger again immediately after acknowledge."""
    m = RootMonitor(min_debounce_s=0.0)
    m.record_change(0.8)
    assert m.check_for_changes() is True
    m.acknowledge()
    m.record_change(0.8)
    assert m.check_for_changes() is True


# ── acknowledge() behavior ────────────────────────────────────────────────────

def test_acknowledge_resets_significance():
    """After acknowledge(), cumulative significance is 0.0."""
    m = RootMonitor(min_debounce_s=0.0)
    m.record_change(0.8)
    m.acknowledge()
    assert m.check_for_changes() is False


def test_acknowledge_resets_poll_interval_to_fast():
    """After acknowledge(), poll_interval resets to 5.0 (fast polling)."""
    m = RootMonitor(min_debounce_s=0.0)
    # Back off the interval manually
    m._poll_schedule_idx = 3  # 30s interval
    m.acknowledge()
    assert m.poll_interval == 5.0


# ── reset() behavior ─────────────────────────────────────────────────────────

def test_reset_clears_all_state():
    """reset() fully clears cumulative significance, trigger time, poll schedule."""
    m = RootMonitor(min_debounce_s=0.0)
    m.record_change(0.8)
    m._poll_schedule_idx = 2
    m._last_poll_time = 99999.0
    m.reset()
    assert m._cumulative_significance == 0.0
    assert m._poll_schedule_idx == 0
    assert m._last_poll_time == 0.0
    assert m.poll_interval == 5.0


# ── poll() with scanner=None ─────────────────────────────────────────────────

def test_poll_with_no_scanner_returns_zero():
    """poll() with scanner=None returns 0.0 significance."""
    m = RootMonitor(scanner=None)
    result = m.poll()
    assert result == 0.0


def test_poll_updates_last_poll_time():
    """poll() updates _last_poll_time to approximately now."""
    m = RootMonitor(scanner=None)
    before = time.monotonic()
    m.poll()
    after = time.monotonic()
    assert before <= m._last_poll_time <= after


def test_poll_uses_configured_root_uris():
    """poll() forwards configured root URIs to the scanner."""
    calls: list[list[str]] = []

    class Scanner:
        def scan_roots(self, root_uris):
            calls.append(list(root_uris))
            return SimpleNamespace(workspace_confidence=0.8)

    m = RootMonitor(scanner=Scanner(), root_uris=["file:///configured"])
    assert m.root_uris == ["file:///configured"]

    result = m.poll()

    assert result == 0.8
    assert calls == [["file:///configured"]]


def test_poll_accepts_root_override_without_mutating_config():
    """poll(root_uris=...) uses the override and preserves configured roots."""
    calls: list[list[str]] = []

    class Scanner:
        def scan_roots(self, root_uris):
            calls.append(list(root_uris))
            return SimpleNamespace(workspace_confidence=0.75)

    m = RootMonitor(scanner=Scanner(), root_uris=["file:///configured"])
    result = m.poll(root_uris=["file:///override"])

    assert result == 0.75
    assert calls == [["file:///override"]]
    assert m.root_uris == ["file:///configured"]


def test_poll_without_configured_roots_warns_and_skips_scanner(monkeypatch):
    """poll() must not silently no-op when no roots are configured."""
    warnings: list[str] = []

    class Scanner:
        def scan_roots(self, root_uris):
            raise AssertionError("scanner should not be called without roots")

    class DummyLogger:
        def warning(self, message):
            warnings.append(str(message))

    import src.multimcp.retrieval.telemetry.monitor as monitor_mod

    monkeypatch.setattr(monitor_mod, "logger", DummyLogger())

    m = RootMonitor(scanner=Scanner(), root_uris=[])
    result = m.poll()

    assert result == 0.0
    assert warnings and "no root URIs configured" in warnings[0]


# ── should_poll() timing ─────────────────────────────────────────────────────

def test_should_poll_true_initially():
    """should_poll() returns True when never polled (last_poll_time=0.0)."""
    m = RootMonitor()
    assert m.should_poll() is True


def test_should_poll_false_immediately_after_poll():
    """should_poll() returns False immediately after a poll."""
    m = RootMonitor(scanner=None)
    m.poll()
    assert m.should_poll() is False


# ── Adaptive backoff via poll() ───────────────────────────────────────────────

def test_adaptive_backoff_after_idle_polls():
    """After multiple idle polls, poll_interval backs off to next step."""
    m = RootMonitor(scanner=None)
    # Force last_poll_time to allow polling immediately
    m._last_poll_time = 0.0
    # First idle poll
    m.poll()
    m._last_poll_time = 0.0
    # Second idle poll — should back off (threshold: 2 consecutive idle polls)
    m.poll()
    assert m.poll_interval > 5.0, f"Expected backoff but got {m.poll_interval}"


def test_adaptive_backoff_caps_at_30s():
    """poll_interval caps at 30.0 seconds regardless of idle poll count."""
    m = RootMonitor(scanner=None)
    # Force many idle polls by resetting last_poll_time each time
    for _ in range(20):
        m._last_poll_time = 0.0
        m.poll()
    assert m.poll_interval == 30.0


# ── Custom significance threshold ─────────────────────────────────────────────

def test_custom_significance_threshold():
    """RootMonitor respects custom significance_threshold."""
    m = RootMonitor(significance_threshold=0.5, min_debounce_s=0.0)
    m.record_change(0.5)
    assert m.check_for_changes() is True


def test_custom_threshold_higher_not_triggered():
    """With threshold=0.9, 0.8 cumulative does not trigger."""
    m = RootMonitor(significance_threshold=0.9, min_debounce_s=0.0)
    m.record_change(0.8)
    assert m.check_for_changes() is False


# ── Package-level import (TELEM-05 export) ───────────────────────────────────

def test_root_monitor_importable_from_package():
    """from src.multimcp.retrieval.telemetry import RootMonitor must work."""
    from src.multimcp.retrieval.telemetry import RootMonitor as _RM
    assert _RM is RootMonitor
