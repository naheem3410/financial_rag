"""Per-provider rate limiting.

Sliding-window limiter over requests-per-minute, tokens-per-minute, and
requests-per-day — enforced with blocking backoff (`acquire` sleeps until
within every configured limit), never by silently dropping or reordering a
call. Thread-safe, since `llm.call_sites.*.concurrency` (config/schema.py)
allows more than one in-flight call per call site.

This exists because provider rate caps (requests/day, requests/minute,
tokens/minute) are well below what a full indexing run needs if every page
fires its own call. Page-batching (offline/propositions/segmenter_llm.py)
plus this limiter are what keep a full run inside the cap.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional


class RateLimiter:
    def __init__(self, rpm: Optional[int] = None, tpm: Optional[int] = None, rpd: Optional[int] = None):
        self._rpm = rpm
        self._tpm = tpm
        self._rpd = rpd
        self._lock = threading.Lock()
        self._request_times: deque[float] = deque()
        self._token_events: deque[tuple[float, int]] = deque()
        self._day_request_times: deque[float] = deque()

    def acquire(self, estimated_tokens: int = 0) -> None:
        """Blocks until a slot is available under every configured limit,
        then reserves it atomically before returning."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._evict(now)
                if self._within_limits(estimated_tokens):
                    self._request_times.append(now)
                    self._day_request_times.append(now)
                    if self._tpm is not None:
                        self._token_events.append((now, estimated_tokens))
                    return
                wait = self._time_until_capacity(now)
            time.sleep(max(wait, 0.05))

    def _evict(self, now: float) -> None:
        minute_ago = now - 60
        day_ago = now - 86400
        while self._request_times and self._request_times[0] < minute_ago:
            self._request_times.popleft()
        while self._token_events and self._token_events[0][0] < minute_ago:
            self._token_events.popleft()
        while self._day_request_times and self._day_request_times[0] < day_ago:
            self._day_request_times.popleft()

    def _within_limits(self, estimated_tokens: int) -> bool:
        if self._rpm is not None and len(self._request_times) >= self._rpm:
            return False
        if self._rpd is not None and len(self._day_request_times) >= self._rpd:
            return False
        if self._tpm is not None:
            used = sum(tokens for _, tokens in self._token_events)
            if used + estimated_tokens > self._tpm:
                return False
        return True

    def current_tpm_usage(self) -> int:
        """Thread-safe snapshot of tokens counted in the current 60s sliding
        window — for observability (periodic logging during a run), not
        enforcement. Reuses the same eviction/window logic `acquire`
        already maintains rather than tracking a second, separate counter."""
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return sum(tokens for _, tokens in self._token_events)

    def _time_until_capacity(self, now: float) -> float:
        candidates: list[float] = []
        if self._rpm is not None and self._request_times:
            candidates.append(60 - (now - self._request_times[0]))
        if self._rpd is not None and self._day_request_times:
            candidates.append(86400 - (now - self._day_request_times[0]))
        if self._tpm is not None and self._token_events:
            candidates.append(60 - (now - self._token_events[0][0]))
        return min(candidates) if candidates else 1.0
