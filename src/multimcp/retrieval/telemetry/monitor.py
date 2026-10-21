"""Root change monitor with adaptive polling and significance-based re-score triggering.

Implements TELEM-05: adaptive polling (5s->10s->20s->30s) with cumulative significance
threshold (>= 0.7 triggers re-score request).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

try:
    from loguru import logger
except ImportError:  # pragma: no cover - dependency-light fallback
    import logging

    logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .scanner import TelemetryScanner

# Adaptive polling schedule: idle polls back off through these intervals (seconds)
_POLL_SCHEDULE = [5.0, 10.0, 20.0, 30.0]
_SIGNIFICANCE_THRESHOLD = 0.7


class RootMonitor:
    """Monitors declared roots for significant workspace changes.

    Uses adaptive polling: starts fast (5s), backs off to 30s when idle.
    Resets to 5s after any significant change.

    Usage:
        monitor = RootMonitor(scanner=my_scanner)
        # On each polling tick:
        if monitor.should_poll():
            monitor.poll()
            if monitor.check_for_changes():
                # Re-score tools
                monitor.acknowledge()
    """

    def __init__(
        self,
        scanner: Optional["TelemetryScanner"] = None,
        significance_threshold: float = _SIGNIFICANCE_THRESHOLD,
        min_debounce_s: float = 10.0,
        root_uris: Optional[list[str]] = None,
    ) -> None:
        self._scanner = scanner
        self._root_uris: list[str] = list(root_uris or [])
        self._threshold = significance_threshold
        self._min_debounce_s = min_debounce_s

        self._poll_schedule_idx: int = 0  # Index into _POLL_SCHEDULE
        self._last_poll_time: float = 0.0
        self._last_trigger_time: float = 0.0
        self._cumulative_significance: float = 0.0
        self._idle_poll_count: int = 0

    @property
    def poll_interval(self) -> float:
        """Current polling interval in seconds."""
        return _POLL_SCHEDULE[min(self._poll_schedule_idx, len(_POLL_SCHEDULE) - 1)]

    def should_poll(self) -> bool:
        """Return True if enough time has passed since last poll."""
        return (time.monotonic() - self._last_poll_time) >= self.poll_interval

    @property
    def root_uris(self) -> list[str]:
        """Configured root URIs that poll() will scan."""
        return list(self._root_uris)

    def set_root_uris(self, root_uris: list[str]) -> None:
        """Replace the configured root URIs used by poll()."""
        self._root_uris = list(root_uris)

    def poll(self, root_uris: Optional[list[str]] = None) -> float:
        """Scan roots and return detected change significance (0.0-1.0).

        Backs off polling interval if no significant change found.
        Resets interval after significant change.

        Args:
            root_uris: Optional override for the roots to scan on this poll.
        """
        self._last_poll_time = time.monotonic()

        if self._scanner is None:
            # No scanner: accumulate nothing, back off on idle
            significance = 0.0
        else:
            active_root_uris = list(root_uris) if root_uris is not None else self._root_uris
            if not active_root_uris:
                logger.warning("RootMonitor.poll() skipped: no root URIs configured")
                significance = 0.0
            else:
                try:
                    evidence = self._scanner.scan_roots(active_root_uris)
                    significance = self._estimate_significance(evidence)
                except Exception as exc:
                    logger.warning(f"Scanner failed during poll: {exc}")
                    significance = 0.0

        self.record_change(significance)

        if significance < self._threshold * 0.3:
            # Idle: back off polling
            self._idle_poll_count += 1
            if self._idle_poll_count >= 2:
                self._poll_schedule_idx = min(
                    self._poll_schedule_idx + 1, len(_POLL_SCHEDULE) - 1
                )
                self._idle_poll_count = 0
        else:
            # Activity detected: reset to fast polling
            self._poll_schedule_idx = 0
            self._idle_poll_count = 0

        return significance

    def record_change(self, significance: float) -> None:
        """Accumulate change significance for threshold evaluation."""
        self._cumulative_significance += max(0.0, significance)

    def check_for_changes(self) -> bool:
        """Return True if cumulative significance has met the threshold.

        Respects debounce: won't return True again within min_debounce_s of the
        last time this method returned True (or last acknowledge).
        Does NOT reset state -- call acknowledge() to reset after handling.
        """
        if self._cumulative_significance < self._threshold:
            return False

        now = time.monotonic()
        if (now - self._last_trigger_time) < self._min_debounce_s:
            return False  # Still in debounce window

        # Record emission time so repeated calls within debounce window return False
        self._last_trigger_time = now
        return True

    def acknowledge(self) -> None:
        """Reset significance accumulator and record trigger time after re-score."""
        self._cumulative_significance = 0.0
        self._last_trigger_time = time.monotonic()
        self._poll_schedule_idx = 0  # Reset to fast polling after change
        self._idle_poll_count = 0

    def reset(self) -> None:
        """Full reset -- clears all state."""
        self._poll_schedule_idx = 0
        self._last_poll_time = 0.0
        self._last_trigger_time = 0.0
        self._cumulative_significance = 0.0
        self._idle_poll_count = 0

    def _estimate_significance(self, evidence: object) -> float:
        """Derive change significance from WorkspaceEvidence.

        Uses workspace_confidence as a proxy for change magnitude.
        Returns 0.0 if evidence is None or has no confidence attribute.
        """
        if evidence is None:
            return 0.0
        confidence = getattr(evidence, "workspace_confidence", None)
        if confidence is None:
            return 0.0
        # Significance is change in confidence -- without prior baseline this is just the value
        return float(confidence)
