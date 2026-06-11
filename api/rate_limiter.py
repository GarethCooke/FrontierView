"""
In-memory per-IP rate limiter for the public /agent endpoint.

Single-process assumption: counters reset on process restart or Render spin-down.
This is by design — Render's ephemeral filesystem makes cross-process state
unreliable, and the key-level monthly spend cap is the financial backstop.
Not a security control: X-Forwarded-For is client-spoofable; IP isolation is
UX throttling only.

Both windows are anchored at the IP's first counted request (and re-anchor at
the first request after a window lapses), not aligned to the UTC calendar: the
day window is a rolling 24h from that anchor — it will not reset at midnight.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

_MINUTE_SECONDS = 60
_DAY_SECONDS = 86_400
_MINUTE_LIMIT = 5
_DAY_LIMIT = 50
_EVICT_THRESHOLD = 10_000


@dataclass
class _Window:
    count: int = 0
    start: float = 0.0


@dataclass
class _IPState:
    minute: _Window = field(default_factory=_Window)
    day: _Window = field(default_factory=_Window)


class RateLimiter:
    """
    Fixed-window per-IP limiter: 5 req/min and 50 req/day on POST /agent.
    Each window is anchored at the IP's first counted request and re-anchors at
    the first request after it lapses — the day window is a rolling 24h from
    that anchor, not aligned to the UTC calendar (it will not reset at midnight).

    Pass a ``clock`` callable (returning epoch seconds) for deterministic
    testing — no sleeps required.  Guards with a threading.Lock because
    FastAPI handlers may interleave across worker threads.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        import time as _time
        self._clock = clock if clock is not None else _time.time
        self._lock = threading.Lock()
        self._ips: dict[str, _IPState] = {}

    def check(self, ip: str) -> tuple[bool, int]:
        """
        Check and consume one slot for ``ip``.

        Returns ``(allowed, retry_after_seconds)``.  ``retry_after_seconds``
        is 0 when allowed.
        """
        with self._lock:
            now = self._clock()
            self._maybe_evict(now)

            if ip not in self._ips:
                self._ips[ip] = _IPState(
                    minute=_Window(count=0, start=now),
                    day=_Window(count=0, start=now),
                )
            state = self._ips[ip]

            if now - state.minute.start >= _MINUTE_SECONDS:
                state.minute = _Window(count=0, start=now)
            if now - state.day.start >= _DAY_SECONDS:
                state.day = _Window(count=0, start=now)

            if state.minute.count >= _MINUTE_LIMIT:
                retry = int(_MINUTE_SECONDS - (now - state.minute.start)) + 1
                return False, retry

            if state.day.count >= _DAY_LIMIT:
                retry = int(_DAY_SECONDS - (now - state.day.start)) + 1
                return False, retry

            state.minute.count += 1
            state.day.count += 1
            return True, 0

    def _maybe_evict(self, now: float) -> None:
        if len(self._ips) < _EVICT_THRESHOLD:
            return
        stale = [ip for ip, s in self._ips.items()
                 if now - s.day.start >= _DAY_SECONDS]
        for ip in stale:
            del self._ips[ip]
