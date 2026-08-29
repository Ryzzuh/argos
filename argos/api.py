"""OCAPI client with a rate limiter shaped to the API's actual behaviour.

Measured against the live endpoint: the limiter is a rolling window that allows
**41 requests**, then returns HTTP 429 for about **45 seconds** before serving
normally again. It cares about volume-per-window, not instantaneous rate — 0.9
req/s and 1.5 req/s both hit the wall at roughly the same request number, and 3
concurrent requests trip a separate burst rule immediately.

So the strategy is: stay just under the window allowance, spread requests two at
a time, and when a 429 does land, wait out the full penalty rather than
hammering. A complete 79-request sweep takes ~2-3 windows, around 3 minutes.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from typing import Any

import httpx

from .config import API_BASE, FILM_ID, SITE_ID
from .health import SweepError
from .token import get_token

MAX_CONCURRENCY = 2          # 3 trips a burst rule on every request
MIN_SPACING = 0.30           # seconds between request starts
WINDOW_SECONDS = 60.0
WINDOW_LIMIT = 35            # measured ceiling is 41; leave headroom
PENALTY_SECONDS = 45.0       # observed 429 lockout
MAX_ATTEMPTS = 5


class _WindowLimiter:
    """Rolling-window limiter: at most `limit` starts per `window` seconds."""

    def __init__(self, limit: int, window: float, spacing: float):
        self._limit = limit
        self._window = window
        self._spacing = spacing
        self._starts: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._resume_at = 0.0

    def penalise(self, seconds: float) -> None:
        """Called after a 429: block every worker until the lockout expires."""
        self._resume_at = max(self._resume_at, time.monotonic() + seconds)

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()

                if now < self._resume_at:
                    wait = self._resume_at - now
                else:
                    while self._starts and now - self._starts[0] > self._window:
                        self._starts.popleft()

                    wait = 0.0
                    if len(self._starts) >= self._limit:
                        wait = self._window - (now - self._starts[0]) + 0.5
                    elif self._starts:
                        gap = now - self._starts[-1]
                        if gap < self._spacing:
                            wait = self._spacing - gap

                    if wait <= 0:
                        self._starts.append(now)
                        return
            await asyncio.sleep(wait)


class Ocapi:
    def __init__(self, token: str | None = None):
        # Deliberately does NOT mint here. Minting drives Playwright's *sync*
        # API, and this object is constructed inside the running event loop, so
        # doing it here raises "Sync API inside the asyncio loop" the moment the
        # cached token expires. Acquired in __aenter__ off-thread instead.
        self._token = token
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._limiter = _WindowLimiter(WINDOW_LIMIT, WINDOW_SECONDS, MIN_SPACING)
        self._client: httpx.AsyncClient | None = None
        self._refreshed = False
        self._lock = asyncio.Lock()
        self.throttled = 0

    async def __aenter__(self) -> "Ocapi":
        if self._token is None:
            self._token = await asyncio.to_thread(get_token)
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(25.0),
            limits=httpx.Limits(max_connections=MAX_CONCURRENCY),
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def _refresh_token(self) -> None:
        """Mint once per client, so parallel 401s don't each start a browser."""
        async with self._lock:
            if self._refreshed:
                return
            self._refreshed = True
            self._token = await asyncio.to_thread(get_token, force=True)

    async def get(self, path: str, *, params: dict | None = None) -> Any:
        assert self._client is not None, "use Ocapi as an async context manager"
        last = "no attempts made"

        for attempt in range(MAX_ATTEMPTS):
            await self._limiter.acquire()
            try:
                async with self._sem:
                    resp = await self._client.get(
                        path, params=params,
                        headers={"Authorization": f"Bearer {self._token}"},
                    )
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.8 * (2 ** attempt) + random.uniform(0, 0.4))
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 204:
                return None

            if resp.status_code == 429:
                self.throttled += 1
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else PENALTY_SECONDS
                except ValueError:
                    wait = PENALTY_SECONDS
                self._limiter.penalise(wait + random.uniform(0, 3))
                last = "HTTP 429 (rate limited)"
                continue

            if resp.status_code == 401 and not self._refreshed:
                await self._refresh_token()
                last = "HTTP 401, refreshed token"
                continue

            last = f"HTTP {resp.status_code}"
            if resp.status_code in (400, 404):
                break

        raise SweepError(f"GET {path} failed after {MAX_ATTEMPTS} attempts: {last}")

    # ---- endpoint wrappers -------------------------------------------------

    async def films(self) -> list[dict]:
        return ((await self.get("/films")) or {}).get("films", [])

    async def screening_dates(self, film_id: str = FILM_ID) -> list[str]:
        data = await self.get("/film-screening-dates",
                              params={"filmIds": film_id, "siteIds": SITE_ID})
        return [d["businessDate"] for d in (data or {}).get("filmScreeningDates", [])]

    async def showtimes_on(self, business_date: str, film_id: str = FILM_ID) -> list[dict]:
        data = await self.get(f"/showtimes/by-business-date/{business_date}",
                              params={"filmIds": film_id, "siteIds": SITE_ID})
        return (data or {}).get("showtimes", [])

    async def seat_layout(self, layout_id: str) -> dict:
        data = await self.get(f"/seat-layouts/{layout_id}")
        if not data or "seatLayout" not in data:
            raise SweepError(f"seat layout {layout_id} returned no data")
        return data["seatLayout"]

    async def seat_availability(self, showtime_id: str) -> list[dict]:
        data = await self.get(f"/showtimes/{showtime_id}/seat-availability")
        return (data or {}).get("seatAvailabilities", [])
