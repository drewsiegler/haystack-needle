"""Polite HTTP: an honest user agent, at most one request a second per host,
a few retries, and an optional on-disk cache for the backtest."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

VERSION = "0.1"
USER_AGENT = (
    f"Mozilla/5.0 (compatible; HaystackNeedle/{VERSION}; "
    "+https://github.com/drewsiegler/haystack-needle)"
)
RETRIES = 3


class Fetcher:
    def __init__(self, cache_dir: Path | None = None, min_interval: float = 1.0,
                 client: httpx.Client | None = None, sleep=time.sleep, clock=time.monotonic):
        self.client = client or httpx.Client(timeout=60, follow_redirects=True)
        self.client.headers["User-Agent"] = USER_AGENT
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self.requests = 0  # network requests made, not cache hits
        self._last: dict[str, float] = {}

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.min_interval - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def get(self, url: str, params: dict | None = None) -> bytes:
        full = str(httpx.URL(url, params=params))
        cached = None
        if self.cache_dir is not None:
            cached = self.cache_dir / hashlib.sha256(full.encode()).hexdigest()[:40]
            if cached.exists():
                return cached.read_bytes()
        host = urlsplit(full).hostname or ""
        for attempt in range(1, RETRIES + 1):
            self._wait_turn(host)
            self.requests += 1
            try:
                response = self.client.get(full)
            except httpx.TransportError:
                if attempt == RETRIES:
                    raise
                self.sleep(5 * attempt)
                continue
            if (response.status_code >= 500 or response.status_code == 429) and attempt < RETRIES:
                self.sleep(5 * attempt)
                continue
            response.raise_for_status()
            break
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(response.content)
        return response.content

    def get_json(self, url: str, params: dict | None = None):
        return json.loads(self.get(url, params))
