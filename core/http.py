"""Polite HTTP client shared by all adapters.

Politeness rules (these keep the tool from getting IP-blocked, which would
kill the user's monitoring):
- descriptive User-Agent with a contact address
- >= 2 seconds between requests to the same host
- in-run response cache so repeated URLs cost one request
- robots.txt honored for scraped (non-API) hosts
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.robotparser

import requests

log = logging.getLogger("monitor.http")

HOST_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT = 60
RETRIES = 2


class PoliteClient:
    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_hit: dict[str, float] = {}
        self._cache: dict[str, requests.Response] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._crawl_delay: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc
        interval = max(HOST_INTERVAL_SECONDS, self._crawl_delay.get(host, 0))
        wait = self._last_hit.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _cache_key(self, method: str, url: str, params) -> str:
        return f"{method} {url} {sorted(params.items()) if params else ''}"

    def request(self, method: str, url: str, *, params=None, headers=None,
                timeout=DEFAULT_TIMEOUT, cache=True) -> requests.Response:
        key = self._cache_key(method, url, params)
        if cache and key in self._cache:
            return self._cache[key]
        last_exc: Exception | None = None
        for attempt in range(RETRIES + 1):
            self._throttle(url)
            try:
                resp = self.session.request(method, url, params=params,
                                            headers=headers, timeout=timeout)
                if resp.status_code >= 500 and attempt < RETRIES:
                    log.warning("%s %s -> %s, retrying", method, url, resp.status_code)
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                if cache:
                    self._cache[key] = resp
                return resp
            except requests.HTTPError as exc:
                # 4xx (except 429) is deterministic — retrying just hammers
                # the server with a request we know is rejected
                status = exc.response.status_code if exc.response is not None else 0
                if 400 <= status < 500 and status != 429:
                    raise
                last_exc = exc
                if attempt < RETRIES:
                    log.warning("%s %s failed (%s), retrying", method, url, exc)
                    time.sleep(2 * (attempt + 1))
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < RETRIES:
                    log.warning("%s %s failed (%s), retrying", method, url, exc)
                    time.sleep(2 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)

    def allowed_by_robots(self, url: str) -> bool:
        """Check robots.txt. Used by scraping adapters (Tier 2); documented
        APIs don't need it but scrapes of ordinary web pages do."""
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            robots_text = ""
            try:
                resp = self.get(f"{parts.scheme}://{host}/robots.txt", cache=True)
                robots_text = resp.text
                rp.parse(robots_text.splitlines())
            except Exception:
                rp.parse([])  # unreadable robots.txt -> allow, per convention
            self._robots[host] = rp
            agent = self.session.headers.get("User-Agent", "*").split("/")[0]
            # robotparser only honors Crawl-delay inside a User-agent group;
            # sites like SITLA put it at the top of the file, so also read it
            # directly and honor the largest value found
            delays = [rp.crawl_delay(agent) or 0, rp.crawl_delay("*") or 0]
            delays += [float(m) for m in
                       re.findall(r"(?im)^\s*crawl-delay:\s*(\d+(?:\.\d+)?)", robots_text)]
            if max(delays) > 0:
                self._crawl_delay[host] = float(max(delays))
        agent = self.session.headers.get("User-Agent", "*").split("/")[0]
        return self._robots[host].can_fetch(agent, url)
