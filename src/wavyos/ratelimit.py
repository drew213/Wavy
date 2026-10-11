"""A sliding-window limiter for the public account endpoints (sign-up, sign-in).

In memory, per process: enough to stop password guessing against one web instance. With several
instances behind a load balancer each keeps its own count, so move this to the database or Redis
before scaling the web tier out wide.
"""
from __future__ import annotations

import threading
import time
from collections import deque


class Limiter:
    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record an attempt for key; False if it is over the limit for the window."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(now)
            return True

    def _prune(self, now: float) -> None:
        for k in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]:
            del self._hits[k]
