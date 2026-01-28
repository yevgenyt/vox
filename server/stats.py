"""Transcriber statistics tracking."""

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class TranscriberStats:
    """Thread-safe statistics for transcription activity."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Counters
    total_transcriptions: int = 0
    total_errors: int = 0

    # Current state
    is_busy: bool = False
    current_start_time: float | None = None

    # Recent history (for calculating rates and averages)
    # Stores (timestamp, duration_ms) tuples
    _recent_transcriptions: deque = field(default_factory=lambda: deque(maxlen=100))

    # Startup time
    _start_time: float = field(default_factory=time.time)

    def start_transcription(self):
        """Mark a transcription as started."""
        with self._lock:
            self.is_busy = True
            self.current_start_time = time.time()

    def end_transcription(self, duration_ms: int, success: bool = True):
        """Mark a transcription as completed."""
        with self._lock:
            self.is_busy = False
            self.current_start_time = None
            self.total_transcriptions += 1

            if success:
                self._recent_transcriptions.append((time.time(), duration_ms))
            else:
                self.total_errors += 1

    def get_stats(self, window_seconds: int = 60) -> dict:
        """
        Get current statistics.

        Args:
            window_seconds: Time window for rate calculations

        Returns:
            dict with current stats
        """
        with self._lock:
            now = time.time()
            uptime_seconds = now - self._start_time
            cutoff = now - window_seconds

            # Filter recent transcriptions within window
            recent = [(ts, dur) for ts, dur in self._recent_transcriptions if ts > cutoff]
            count_in_window = len(recent)

            # Calculate average duration
            avg_duration_ms = 0
            if recent:
                avg_duration_ms = sum(dur for _, dur in recent) / len(recent)

            # Calculate current transcription duration if busy
            current_duration_ms = None
            if self.is_busy and self.current_start_time:
                current_duration_ms = int((now - self.current_start_time) * 1000)

            return {
                "total_transcriptions": self.total_transcriptions,
                "total_errors": self.total_errors,
                "is_busy": self.is_busy,
                "current_duration_ms": current_duration_ms,
                "transcriptions_last_window": count_in_window,
                "window_seconds": window_seconds,
                "avg_duration_ms": int(avg_duration_ms),
                "uptime_seconds": int(uptime_seconds),
            }


# Global instance
transcriber_stats = TranscriberStats()
