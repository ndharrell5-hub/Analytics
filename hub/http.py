"""Shared HTTP layer: per-source rate limiting, retries with backoff, and response caching."""

from __future__ import annotations

import time
from typing import Any

import requests

from hub.cache import ResponseCache

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
RETRY_STATUSES = (429, 500, 502, 503, 504)


class SourceUnavailable(RuntimeError):
    """A source could not be reached after all retries."""


class CachedFetcher:
    def __init__(
        self,
        source: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        cache: ResponseCache | None = None,
        session: requests.Session | None = None,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        retries: int = 3,
        unavailable_hint: str = "",
        error_cls: type[Exception] = SourceUnavailable,
    ):
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.cache = cache or ResponseCache()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": BROWSER_UA, **(headers or {})})
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.unavailable_hint = unavailable_hint
        self.error_cls = error_cls
        self._last_request = 0.0

    def get(self, path: str, params: dict[str, Any] | None = None,
            ttl: float | None = 6 * 3600, as_json: bool = True) -> Any:
        """GET base_url/path. Returns parsed JSON (as_json) or text. ttl=None caches forever."""
        params = params or {}
        key = ResponseCache.make_key(self.source, path, params)
        cached = self.cache.get(key, ttl)
        if cached is not None:
            return cached

        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Exception | None = None
        for attempt in range(self.retries):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as err:
                last_err = err
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in RETRY_STATUSES:
                last_err = RuntimeError(f"HTTP {resp.status_code}")
                # Respect Retry-After when a site tells us how long to back off.
                retry_after = getattr(resp, "headers", {}).get("Retry-After", "")
                time.sleep(min(int(retry_after), 120) if retry_after.isdigit()
                           else 2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            if not as_json and (resp.encoding or "").lower() in ("", "iso-8859-1"):
                # Sites that omit a charset get latin-1 from requests; they're really UTF-8.
                resp.encoding = "utf-8"
            body = resp.json() if as_json else resp.text
            self.cache.set(key, body)
            return body

        hint = f" {self.unavailable_hint}" if self.unavailable_hint else ""
        raise self.error_cls(
            f"Could not reach {self.source} ({path}) after {self.retries} tries "
            f"({last_err}).{hint}"
        )
