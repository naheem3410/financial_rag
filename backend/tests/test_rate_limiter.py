"""RateLimiter tests."""
from __future__ import annotations

import threading
import time

from llm.rate_limiter import RateLimiter


def test_unlimited_acquires_immediately():
    limiter = RateLimiter()

    start = time.monotonic()
    for _ in range(50):
        limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5


def test_rpm_limit_is_enforced_within_the_cap():
    limiter = RateLimiter(rpm=5)

    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5  # first 5 requests should not block at all


def test_rpm_limit_blocks_the_next_request_until_a_slot_frees():
    limiter = RateLimiter(rpm=2)
    # Manually seed two "requests" 0.2s ago so the window has 0.8s left.
    now = time.monotonic()
    limiter._request_times.append(now - 59.8)
    limiter._request_times.append(now - 59.8)
    limiter._day_request_times.append(now - 59.8)
    limiter._day_request_times.append(now - 59.8)

    wait = limiter._time_until_capacity(now)

    assert 0 < wait <= 1.0  # a fraction of a second left in the 60s window from t=-59.8


def test_tpm_limit_prevents_exceeding_the_budget():
    limiter = RateLimiter(tpm=100)

    limiter.acquire(estimated_tokens=90)

    assert limiter._within_limits(estimated_tokens=20) is False
    assert limiter._within_limits(estimated_tokens=5) is True


def test_current_tpm_usage_reflects_the_sliding_window():
    limiter = RateLimiter(tpm=1000)

    assert limiter.current_tpm_usage() == 0

    limiter.acquire(estimated_tokens=300)
    limiter.acquire(estimated_tokens=150)

    assert limiter.current_tpm_usage() == 450


def test_current_tpm_usage_evicts_events_older_than_a_minute(monkeypatch):
    limiter = RateLimiter(tpm=1000)
    limiter.acquire(estimated_tokens=300)

    # Simulate the event having aged out of the 60s window.
    old_time, tokens = limiter._token_events[0]
    limiter._token_events[0] = (old_time - 61, tokens)

    assert limiter.current_tpm_usage() == 0


def test_rpd_limit_is_tracked_separately_from_rpm():
    limiter = RateLimiter(rpm=1000, rpd=3)

    for _ in range(3):
        limiter.acquire()

    assert limiter._within_limits(estimated_tokens=0) is False


def test_thread_safe_under_concurrent_acquire():
    """Multiple threads calling acquire() concurrently must not corrupt internal state."""
    limiter = RateLimiter(rpm=1000)
    errors = []

    def worker():
        try:
            for _ in range(20):
                limiter.acquire()
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert errors == []
    assert len(limiter._request_times) == 160
