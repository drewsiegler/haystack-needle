"""Polite HTTP: an honest user agent, at most one request a second per host
(slower where a host's robots.txt sets a Crawl-delay), a few retries, an
optional on-disk cache for the backtest, and no more requests to a host that
answers with a bot check."""

from __future__ import annotations

import hashlib
import json
import re
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
# Seconds between requests, from each host's robots.txt Crawl-delay or, for
# eScribe, from its bot check: it refused files after 15 at one a second. A key
# covers that host and everything under it. Hosts not listed get min_interval.
CRAWL_DELAYS = {"iqm2.com": 60.0, "escribemeetings.com": 10.0}
# Imperva's bot check is an ordinary HTML page, often with a 200 status: "Pardon Our
# Interruption", or the older Incapsula "Request unsuccessful" page. Every page on an
# Imperva host loads its _Incapsula_Resource script, so that alone is no sign. Only
# the start of the page is read: the block page says so at the top.
IMPERVA_BLOCK = re.compile(rb"<title[^>]*>\s*Pardon Our Interruption|Incapsula incident ID", re.I)
BLOCK_PAGE_HEAD = 16_384


class Deferred(httpx.HTTPError):
    """A request left for the next run without being sent: a slow host that would
    have to wait past the run's time for slow hosts, or a host that answered an
    earlier request with a bot check."""


class BotCheck(httpx.HTTPError):
    """The host answered with a bot check (a Cloudflare challenge or Imperva's block
    page) instead of what was asked for. Haystack Needle doesn't try to get past one:
    the host is left alone for the rest of the run."""


def is_bot_check(response: httpx.Response) -> bool:
    if response.headers.get("cf-mitigated") == "challenge":
        return True
    if "html" not in response.headers.get("content-type", "").lower():
        return False
    return IMPERVA_BLOCK.search(response.content[:BLOCK_PAGE_HEAD]) is not None


class Fetcher:
    def __init__(self, cache_dir: Path | None = None, min_interval: float = 1.0,
                 client: httpx.Client | None = None, sleep=time.sleep, clock=time.monotonic,
                 crawl_delays: dict[str, float] = CRAWL_DELAYS, slow_budget: float | None = None):
        self.client = client or httpx.Client(timeout=60, follow_redirects=True)
        self.client.headers["User-Agent"] = USER_AGENT
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self.crawl_delays = crawl_delays
        # Waiting on a crawl delay is allowed only this many seconds from now;
        # after that, a request that would wait raises Deferred. None: no limit.
        self.slow_until = None if slow_budget is None else clock() + slow_budget
        self.requests = 0  # network requests made, not cache hits
        self.deferred = 0  # requests to slow hosts left for the next run
        self.refused: set[str] = set()  # hosts that answered with a bot check this run
        self._last: dict[str, float] = {}

    def interval(self, host: str) -> float:
        for domain, delay in self.crawl_delays.items():
            if host == domain or host.endswith("." + domain):
                return max(delay, self.min_interval)
        return self.min_interval

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            interval = self.interval(host)
            wait = interval - (self.clock() - last)
            if wait > 0:
                if (interval > self.min_interval and self.slow_until is not None
                        and self.clock() + wait > self.slow_until):
                    self.deferred += 1
                    raise Deferred(f"{host} asks for {interval:.0f} s between requests; "
                                   "left for the next run")
                self.sleep(wait)
        self._last[host] = self.clock()

    def get(self, url: str, params: dict | None = None) -> bytes:
        # httpx.URL(url, params=None) would drop a query already in the URL.
        full = str(httpx.URL(url).copy_merge_params(params) if params else httpx.URL(url))
        return self._fetch(full)

    def post_json(self, url: str, body: dict):
        """POST a JSON body and parse the JSON answer, for a platform whose read-only
        calls are POSTs (eScribe's meeting calendar). Same user agent, rate and retries."""
        return json.loads(self._fetch(url, body))

    def _fetch(self, full: str, body: dict | None = None) -> bytes:
        cached = None
        if self.cache_dir is not None:
            cache_key = full if body is None else f"POST {full} {json.dumps(body, sort_keys=True)}"
            cached = self.cache_dir / hashlib.sha256(cache_key.encode()).hexdigest()[:40]
            if cached.exists():
                return cached.read_bytes()
        host = urlsplit(full).hostname or ""
        if host in self.refused:
            raise Deferred(f"{host} answered an earlier request with a bot check; left for the next run")
        for attempt in range(1, RETRIES + 1):
            self._wait_turn(host)
            self.requests += 1
            try:
                response = self.client.get(full) if body is None else self.client.post(full, json=body)
            except httpx.TransportError:
                if attempt == RETRIES:
                    raise
                self.sleep(5 * attempt)
                continue
            if is_bot_check(response):
                self.refused.add(host)
                raise BotCheck(f"{host} answered with a bot check instead of the file or page, so "
                               "nothing more is asked of it this run")
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
