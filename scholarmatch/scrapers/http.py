"""Polite HTTP for all scrapers: robots.txt, per-host rate limit, disk cache, backoff.

Rules from scraper-brief.md, enforced here so no scraper can skip them:
  * re-check robots.txt at the start of every run; refuse disallowed paths
  * <= 1 request per REQUEST_DELAY_SECONDS per host (10 s where robots asks for it), never parallel
  * descriptive User-Agent with contact info
  * retry 429/5xx with exponential backoff, give up after 3 tries
  * cache raw responses so parser fixes never re-hit the site
  * a 403 / challenge page means STOP for that host - never work around it
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .. import config


class Blocked(RuntimeError):
    """Host refused us (403/challenge) or robots.txt disallows the path. Do not retry."""


class SignInRequired(RuntimeError):
    pass


class PoliteFetcher:
    def __init__(self, use_cache: bool = True, cache_dir: Optional[Path] = None):
        self.ua = config.user_agent()
        self.client = httpx.Client(headers={"User-Agent": self.ua}, follow_redirects=True, timeout=30)
        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir or config.CACHE_DIR)
        self._last: dict[str, float] = {}
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self.blocked_hosts: set[str] = set()
        self.stats = {"fetched": 0, "cached": 0, "skipped": []}

    # -- robots ---------------------------------------------------------------
    def _robots_for(self, host: str) -> Optional[RobotFileParser]:
        if host not in self._robots:
            self._wait(host)
            r = self.client.get(f"https://{host}/robots.txt")
            if r.status_code in (401, 403):
                self.blocked_hosts.add(host)
                raise Blocked(f"{host}: robots.txt returned {r.status_code}; site is refusing automated access.")
            rp = RobotFileParser()
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            self._robots[host] = rp
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        host = urlsplit(url).netloc
        rp = self._robots_for(host)
        return rp is None or rp.can_fetch(self.ua, url)

    # -- fetch ----------------------------------------------------------------
    def _wait(self, host: str) -> None:
        delay = config.HOST_DELAYS.get(host, config.REQUEST_DELAY_SECONDS)
        gap = time.monotonic() - self._last.get(host, 0)
        if gap < delay:
            time.sleep(delay - gap)
        self._last[host] = time.monotonic()

    def _cache_path(self, url: str) -> Path:
        host = urlsplit(url).netloc
        return self.cache_dir / host / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")

    def get(self, url: str, refresh: bool = False) -> str:
        """Return page text. Raises Blocked / SignInRequired / httpx.HTTPError."""
        host = urlsplit(url).netloc
        if host in self.blocked_hosts:
            raise Blocked(f"{host} is blocked for this run.")
        path = self._cache_path(url)
        if self.use_cache and not refresh and path.exists():
            self.stats["cached"] += 1
            return path.read_text(encoding="utf-8")
        if not self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")
        last_exc: Optional[Exception] = None
        for attempt in range(config.MAX_RETRIES):
            self._wait(host)
            try:
                r = self.client.get(url)
            except httpx.HTTPError as e:
                last_exc = e
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code in (401, 403):
                self.blocked_hosts.add(host)
                raise Blocked(f"{host} returned {r.status_code} for {url}; stopping (not working around it).")
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                time.sleep(2 ** (attempt + 1))
                continue
            if "/users/sign_in" in str(r.url) or "accounts.google.com" in str(r.url):
                raise SignInRequired(url)
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text, encoding="utf-8")
            self.stats["fetched"] += 1
            return r.text
        raise last_exc or RuntimeError(f"Failed to fetch {url}")

    def close(self) -> None:
        self.client.close()
