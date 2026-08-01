"""42 API client: OAuth2, rate limiting, pagination.

The rate limiter is the important part. 2 req/s and 1200 req/hour is a hard ceiling,
and blowing it costs an hour of a 24-hour hackathon.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)

TOKEN_PATH = "/oauth/token"
# Refresh at 90% of the stated lifetime rather than waiting for a 401 mid-render.
TOKEN_REFRESH_RATIO = 0.9


class RateLimitError(RuntimeError):
    pass


class RateLimiter:
    """Enforces both documented limits: 2 req/s and 1200 req/hour.

    The hourly budget is tracked as a sliding window, not a fixed clock hour, because
    the documented limit behaves as a rolling window and guessing wrong means 429s.
    """

    def __init__(self, per_second: float = 2.0, per_hour: int = 1200, safety: float = 0.9):
        self.min_interval = 1.0 / per_second
        self.per_hour = int(per_hour * safety)
        self._window: deque[float] = deque()
        self._last_call = 0.0
        self._lock = threading.Lock()
        self.total_requests = 0

    def _trim(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()

            gap = self.min_interval - (now - self._last_call)
            if gap > 0:
                time.sleep(gap)
                now = time.monotonic()

            self._trim(now)
            if len(self._window) >= self.per_hour:
                # Sleep until the oldest request ages out of the window.
                sleep_for = self._window[0] + 3600.0 - now
                log.warning(
                    "hourly budget exhausted (%d used), sleeping %.0fs",
                    len(self._window),
                    sleep_for,
                )
                time.sleep(max(sleep_for, 0))
                now = time.monotonic()
                self._trim(now)

            self._window.append(now)
            self._last_call = now
            self.total_requests += 1

    @property
    def used_this_hour(self) -> int:
        with self._lock:
            self._trim(time.monotonic())
            return len(self._window)


class FtClient:
    """Client-credentials client for the 42 API.

    Deliberately synchronous: it runs in a background fetch job, never in a request
    path, so simplicity beats concurrency here.
    """

    def __init__(
        self,
        uid: str,
        secret: str,
        base_url: str = "https://api.intra.42.fr",
        timeout: float = 30.0,
        limiter: RateLimiter | None = None,
        max_retries: int = 4,
    ):
        if not uid or not secret:
            raise ValueError("FT_UID and FT_SECRET must be set (see .env.example)")
        self.uid = uid
        self.secret = secret
        self.limiter = limiter or RateLimiter()
        self.max_retries = max_retries
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._token: str | None = None
        self._expires_at = 0.0
        self.last_headers: httpx.Headers | None = None

    # -- auth ---------------------------------------------------------------

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token

        self.limiter.acquire()
        resp = self._http.post(
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self.uid,
                "client_secret": self.secret,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

        self._token = payload["access_token"]
        lifetime = float(payload.get("expires_in", 7200))
        self._expires_at = time.monotonic() + lifetime * TOKEN_REFRESH_RATIO
        log.info("token acquired, scope=%s lifetime=%.0fs", payload.get("scope"), lifetime)
        return self._token

    def token_info(self) -> dict[str, Any]:
        """Confirms what the token can actually do. First thing to check on a 401."""
        return self.get_json("/oauth/token/info")

    # -- requests -----------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            token = self._ensure_token()
            self.limiter.acquire()
            try:
                resp = self._http.get(
                    path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.RequestError as exc:
                last_exc = exc
                backoff = 2**attempt
                log.warning("network error on %s (%s), retrying in %ds", path, exc, backoff)
                time.sleep(backoff)
                continue

            self.last_headers = resp.headers

            if resp.status_code == 401:
                # Token rejected: drop it and let the next attempt re-acquire.
                log.warning("401 on %s, refreshing token", path)
                self._token = None
                self._expires_at = 0.0
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2**attempt))
                log.warning("429 on %s, backing off %.0fs", path, retry_after)
                time.sleep(retry_after)
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

    def __enter__(self) -> "FtClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
