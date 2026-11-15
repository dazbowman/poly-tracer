"""Polite HTTP GET client: per-host rate limit, retries with backoff, disk cache.

SPEC §0.4: ≤5 req/s per host, exponential backoff, honor Retry-After on 429/503,
descriptive User-Agent, cache everything cacheable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "polycopy-research/0.1 (+https://github.com/dazbowman/poly-tracer; read-only research)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PoliteClient:
    """Synchronous JSON GET client for one host."""

    def __init__(
        self,
        base_url: str,
        *,
        rps: float = 4.0,
        cache_dir: Path | None = None,
        max_retries: int = 6,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = 1.0 / rps
        self._last_request = 0.0
        self.cache_dir = cache_dir
        self.max_retries = max_retries
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
        )

    # -- cache ---------------------------------------------------------------
    def _cache_path(self, url: str, params: dict | None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(
            (url + "?" + json.dumps(params or {}, sort_keys=True)).encode()
        ).hexdigest()
        return self.cache_dir / key[:2] / f"{key}.json"

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        cache_ttl: float | None = None,
    ) -> Any:
        """GET base_url+path, return parsed JSON. cache_ttl seconds (None = no cache)."""
        url = self.base_url + path
        cpath = self._cache_path(url, params) if cache_ttl else None
        if cpath and cpath.exists() and (time.time() - cpath.stat().st_mtime) < cache_ttl:
            with open(cpath, encoding="utf-8") as f:
                return json.load(f)

        body = self._get_with_retries(url, params)

        if cpath:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = cpath.with_suffix(".tmp")
            tmp.write_text(json.dumps(body))
            tmp.replace(cpath)
        return body

    # -- transport -----------------------------------------------------------
    def _throttle(self) -> None:
        wait = self._last_request + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get_with_retries(self, url: str, params: dict | None) -> Any:
        delay = 1.0
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                r = self._client.get(url, params=params)
            except httpx.HTTPError as e:
                if attempt == self.max_retries - 1:
                    raise
                log.warning("GET %s failed (%s), retry in %.1fs", url, type(e).__name__, delay)
                time.sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, 60)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in RETRYABLE_STATUS and attempt < self.max_retries - 1:
                retry_after = r.headers.get("Retry-After")
                try:
                    sleep_for = float(retry_after) if retry_after else delay
                except ValueError:
                    sleep_for = delay
                log.warning("GET %s -> %d, retry in %.1fs", url, r.status_code, sleep_for)
                time.sleep(sleep_for + random.uniform(0, 0.5))
                delay = min(delay * 2, 60)
                continue
            raise httpx.HTTPStatusError(
                f"GET {url} -> {r.status_code}: {r.text[:300]}", request=r.request, response=r
            )
        raise RuntimeError("unreachable")
