# File: tests/test_client.py
"""Unit tests for the throttled Intra request queue (no network)."""

from __future__ import annotations

import threading
import time

from ft.client import ThrottledRequestQueue, parse_retry_after


def test_parse_retry_after_seconds():
    assert parse_retry_after("3", 1.0) == 3.0
    assert parse_retry_after("0", 1.0) == 0.0
    assert parse_retry_after(None, 2.5) == 2.5
    assert parse_retry_after("nope", 1.5) == 1.5


def test_queue_enforces_min_interval():
    q = ThrottledRequestQueue(min_interval=0.15, per_hour=1200, safety=1.0)
    stamps: list[float] = []

    def mark() -> int:
        stamps.append(time.monotonic())
        return len(stamps)

    try:
        assert q.submit(mark) == 1
        assert q.submit(mark) == 2
        assert q.submit(mark) == 3
        gaps = [stamps[i] - stamps[i - 1] for i in range(1, len(stamps))]
        assert all(g >= 0.14 for g in gaps), gaps
        assert q.total_requests == 3
    finally:
        q.close()


def test_queue_buffers_parallel_submitters():
    q = ThrottledRequestQueue(min_interval=0.05, per_hour=1200, safety=1.0)
    results: list[int] = []
    lock = threading.Lock()

    def job(n: int) -> int:
        with lock:
            results.append(n)
        return n

    try:
        threads = [
            threading.Thread(target=lambda i=i: q.submit(lambda: job(i)))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        assert sorted(results) == [0, 1, 2, 3, 4]
        assert q.total_requests == 5
    finally:
        q.close()


def test_pause_for_delays_next_dispatch():
    q = ThrottledRequestQueue(min_interval=0.01, per_hour=1200, safety=1.0)
    try:
        q.submit(lambda: 1)
        q.pause_for(0.2)
        start = time.monotonic()
        q.submit(lambda: 2)
        assert time.monotonic() - start >= 0.18
    finally:
        q.close()
