# File: src/ft/client.py
"""42 API client: OAuth2, throttled request queue, pagination.

All Intra HTTP traffic is serialized through ``ThrottledRequestQueue`` so the
fetch job cannot burst past the public app ceiling of **2 requests/second**
(and the rolling **1200/hour** budget). On HTTP 429 the queue pauses for the
server's ``Retry-After`` (+100ms) before the same call retries — continuous
dashboard sync stays alive instead of cascading failures.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterator, TypeVar

import httpx

log = logging.getLogger(__name__)

TOKEN_PATH = "/oauth/token"
# Refresh at 90% of the stated lifetime rather than waiting for a 401 mid-fetch.
TOKEN_REFRESH_RATIO = 0.9

# Official Intra client-credentials ceiling for an unprivileged app.
DEFAULT_PER_SECOND = 2.0
DEFAULT_MIN_INTERVAL_S = 0.5  # 1 / 2 req/s
DEFAULT_PER_HOUR = 1200
RETRY_AFTER_SAFETY_BUFFER_S = 0.1

T = TypeVar("T")


class RateLimitError(RuntimeError):
    pass


def parse_retry_after(header: str | None, fallback: float) -> float:
    """Parse ``Retry-After`` as delay-seconds or HTTP-date; always return seconds ≥ 0."""
    if header is None or header == "":
        return max(fallback, 0.0)
    try:
        return max(float(header), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delay = (when - datetime.now(timezone.utc)).total_seconds()
        return max(delay, 0.0)
    except (TypeError, ValueError, OverflowError):
        return max(fallback, 0.0)


class ThrottledRequestQueue:
    """Central serial queue for every 42 API HTTP dispatch.

    Callers ``submit`` work units; a single daemon worker executes them in FIFO
    order with a hard minimum spacing of ``min_interval`` (default 500ms ⇒ 2/s).
    When the fetch fan-out produces work faster than Intra allows, jobs remain
    buffered in memory — nothing is dropped, and the sync loop just waits.

    A 429 response pauses *this worker* (hence the whole pipeline) for
    ``Retry-After + 100ms`` before the same unit is retried.
    """

    def __init__(
        self,
        per_second: float = DEFAULT_PER_SECOND,
        per_hour: int = DEFAULT_PER_HOUR,
        safety: float = 0.9,
        min_interval: float | None = None,
    ):
        self.min_interval = (
            float(min_interval) if min_interval is not None else (1.0 / per_second)
        )
        self.per_hour = int(per_hour * safety)
        self._jobs: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = queue.Queue()
        self._window: deque[float] = deque()
        self._last_dispatch = 0.0
        self._pause_until = 0.0
        self._stats_lock = threading.Lock()
        self.total_requests = 0
        self._stopped = False
        self._worker = threading.Thread(
            target=self._run,
            name="ft-api-throttle-queue",
            daemon=True,
        )
        self._worker.start()

    # -- public stats (kept compatible with former RateLimiter surface) ----

    def _trim(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    @property
    def used_this_hour(self) -> int:
        with self._stats_lock:
            self._trim(time.monotonic())
            return len(self._window)

    @property
    def pending(self) -> int:
        return self._jobs.qsize()

    def pause_for(self, seconds: float) -> None:
        """Hold the pipeline for at least ``seconds`` from now (429 / budget)."""
        deadline = time.monotonic() + max(seconds, 0.0)
        with self._stats_lock:
            if deadline > self._pause_until:
                self._pause_until = deadline

    def acquire(self) -> None:
        """Compatibility shim: consume one throttled slot through the queue."""
        self.submit(lambda: None)

    def submit(self, fn: Callable[[], T]) -> T:
        """Enqueue ``fn``, block until the worker finishes it, return its result."""
        if self._stopped:
            raise RuntimeError("ThrottledRequestQueue is closed")
        fut: Future[T] = Future()
        self._jobs.put((fn, fut))
        return fut.result()

    def close(self) -> None:
        self._stopped = True
        self._jobs.put(None)
        self._worker.join(timeout=5.0)

    # -- worker internals --------------------------------------------------

    def _wait_until_dispatch_slot(self) -> float:
        """Block until min-interval, pause window, and hourly budget all allow a send."""
        while True:
            with self._stats_lock:
                now = time.monotonic()
                pause_left = self._pause_until - now
                gap = self.min_interval - (now - self._last_dispatch)
                self._trim(now)
                hour_sleep = 0.0
                if len(self._window) >= self.per_hour:
                    hour_sleep = max(self._window[0] + 3600.0 - now, 0.0)

            sleep_for = max(pause_left, gap, hour_sleep, 0.0)
            if sleep_for <= 0:
                return time.monotonic()

            if hour_sleep > 0 and hour_sleep >= pause_left and hour_sleep >= gap:
                log.warning(
                    "hourly budget exhausted (%d used), queue sleeping %.0fs",
                    self.used_this_hour,
                    hour_sleep,
                )
            time.sleep(sleep_for)

    def _mark_dispatched(self, when: float) -> None:
        with self._stats_lock:
            self._trim(when)
            self._window.append(when)
            self._last_dispatch = when
            self.total_requests += 1

    def _run(self) -> None:
        while True:
            item = self._jobs.get()
            if item is None:
                return
            fn, fut = item
            when = self._wait_until_dispatch_slot()
            try:
                if not fut.set_running_or_notify_cancel():
                    continue
                result = fn()
            except BaseException as exc:  # noqa: BLE001 — surface to caller Future
                fut.set_exception(exc)
            else:
                fut.set_result(result)
            finally:
                # Every attempt counts toward 2/s and 1200/hr — including 429s.
                self._mark_dispatched(when)


# Backward-compatible name used across the codebase / docs.
class RateLimiter(ThrottledRequestQueue):
    """Alias: historical name for the throttled Intra request queue."""


class FtClient:
    """Client-credentials client for the 42 API.

    Deliberately runs in the background fetch job, never on a page render path.
    Every token and GET dispatch is funneled through ``self.limiter`` (the
    throttled queue) so continuous telemetry sync cannot 429 the hour away.
    """

    def __init__(
        self,
        uid: str,
        secret: str,
        base_url: str = "https://api.intra.42.fr",
        timeout: float = 30.0,
        limiter: ThrottledRequestQueue | None = None,
        max_retries: int = 4,
    ):
        if not uid or not secret:
            raise ValueError("FT_UID and FT_SECRET must be set (see .env.example)")
        self.uid = uid
        self.secret = secret
        self.limiter = limiter or ThrottledRequestQueue()
        self.max_retries = max_retries
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = threading.Lock()
        self.last_headers: httpx.Headers | None = None
        self._owns_limiter = limiter is None

    # -- auth ---------------------------------------------------------------

    def _post_token(self) -> dict[str, Any]:
        """Exchange client credentials for a bearer token (queued HTTP POST)."""

        def _send() -> dict[str, Any]:
            resp = self._http.post(
                TOKEN_PATH,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.uid,
                    "client_secret": self.secret,
                },
            )
            if resp.status_code == 429:
                delay = parse_retry_after(
                    resp.headers.get("Retry-After"),
                    fallback=2.0,
                )
                pause = delay + RETRY_AFTER_SAFETY_BUFFER_S
                log.warning(
                    "429 on %s, pausing queue for %.2fs (Retry-After + buffer)",
                    TOKEN_PATH,
                    pause,
                )
                self.limiter.pause_for(pause)
                time.sleep(pause)
                raise RateLimitError("token endpoint returned 429")
            resp.raise_for_status()
            return resp.json()

        # Token acquisition itself is queued so it shares the 2/s budget.
        for attempt in range(self.max_retries):
            try:
                return self.limiter.submit(_send)
            except RateLimitError:
                log.warning("token 429 retry %d/%d", attempt + 1, self.max_retries)
                continue
        raise RateLimitError(f"giving up on {TOKEN_PATH} after {self.max_retries} attempts")

    def _ensure_token(self) -> str:
        with self._token_lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token

            payload = self._post_token()
            self._token = payload["access_token"]
            lifetime = float(payload.get("expires_in", 7200))
            self._expires_at = time.monotonic() + lifetime * TOKEN_REFRESH_RATIO
            log.info(
                "token acquired, scope=%s lifetime=%.0fs",
                payload.get("scope"),
                lifetime,
            )
            return self._token

    def token_info(self) -> dict[str, Any]:
        """Confirms what the token can actually do. First thing to check on a 401."""
        return self.get_json("/oauth/token/info")

    # -- requests -----------------------------------------------------------

    def _queued_get(
        self,
        path: str,
        params: dict[str, Any] | None,
        token: str,
        attempt: int,
    ) -> httpx.Response:
        """One throttled GET attempt; 429 pauses the whole queue then re-raises."""

        def _send() -> httpx.Response:
            resp = self._http.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.last_headers = resp.headers

            if resp.status_code == 429:
                delay = parse_retry_after(
                    resp.headers.get("Retry-After"),
                    fallback=float(2**attempt),
                )
                pause = delay + RETRY_AFTER_SAFETY_BUFFER_S
                log.warning(
                    "429 on %s, pausing queue for %.2fs (Retry-After + buffer)",
                    path,
                    pause,
                )
                self.limiter.pause_for(pause)
                time.sleep(pause)
                raise RateLimitError(f"429 on {path}")

            return resp

        return self.limiter.submit(_send)

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            token = self._ensure_token()
            try:
                resp = self._queued_get(path, params, token, attempt)
            except httpx.RequestError as exc:
                last_exc = exc
                backoff = 2**attempt
                log.warning(
                    "network error on %s (%s), retrying in %ds",
                    path,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
                continue
            except RateLimitError as exc:
                last_exc = exc
                log.warning(
                    "rate-limited on %s (attempt %d/%d), retrying",
                    path,
                    attempt + 1,
                    self.max_retries,
                )
                continue

            if resp.status_code == 401:
                log.warning("401 on %s, refreshing token", path)
                with self._token_lock:
                    self._token = None
                    self._expires_at = 0.0
                continue

            if resp.status_code >= 500:
                backoff = 2**attempt
                log.warning("%d on %s, retrying in %ds", resp.status_code, path, backoff)
                time.sleep(backoff)
                continue

            resp.raise_for_status()
            return resp

        if last_exc:
            raise last_exc
        raise RateLimitError(f"giving up on {path} after {self.max_retries} attempts")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(path, params).json()

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yields items across pages.

        Uses X-Total to know when to stop instead of probing for an empty page,
        which saves one request per collection.
        """
        params = dict(params or {})
        params["page[size]"] = page_size
        page = 1
        total: int | None = None

        while True:
            params["page[number]"] = page
            resp = self.get(path, params)
            batch = resp.json()

            if not isinstance(batch, list):
                raise TypeError(f"{path} did not return a list; not a paginated endpoint")
            if not batch:
                return

            yield from batch

            if total is None:
                raw_total = resp.headers.get("X-Total")
                total = int(raw_total) if raw_total and raw_total.isdigit() else None

            if total is not None and page * page_size >= total:
                return
            if max_pages is not None and page >= max_pages:
                log.warning("%s hit max_pages=%d, results truncated", path, max_pages)
                return
            if total is None and len(batch) < page_size:
                return

            page += 1

    def close(self) -> None:
        self._http.close()
        if self._owns_limiter:
            self.limiter.close()

    def __enter__(self) -> "FtClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
