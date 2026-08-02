"""Resilient list downloads with cached-copy fallback.

All publishers redirect to signed, short-lived URLs (UN -> Azure blob,
OFAC -> S3), so requests always follow redirects and only the canonical URL
is ever recorded. OFAC rejects HEAD requests (405), so everything is a GET.
On any live failure the most recent cached copy in data/cache/ is used and
the fallback is reported so the ingestion log can record it.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


class DownloadError(RuntimeError):
    """Live download failed and no cached copy exists."""


@dataclass
class FetchResult:
    path: Path
    url: str
    sha256: str
    from_cache: bool


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(
    url: str,
    cache_path: Path,
    *,
    offline: bool = False,
    attempts: int = 3,
    timeout: float = 120.0,
    client: httpx.Client | None = None,
) -> FetchResult:
    """Download url to cache_path, or fall back to the existing cached copy.

    Returns from_cache=True when the live download was skipped (offline) or
    failed; raises DownloadError when it failed and no cache exists.
    """
    if offline:
        if cache_path.is_file():
            return FetchResult(cache_path, url, _sha256(cache_path), from_cache=True)
        raise DownloadError(f"offline requested but no cached copy at {cache_path}")

    last_error: Exception | None = None
    own_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=timeout)
    try:
        for attempt in range(1, attempts + 1):
            try:
                response = http.get(url)
                response.raise_for_status()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
                tmp.write_bytes(response.content)
                tmp.replace(cache_path)
                return FetchResult(cache_path, url, _sha256(cache_path), from_cache=False)
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(2**attempt)
    finally:
        if own_client:
            http.close()

    if cache_path.is_file():
        return FetchResult(cache_path, url, _sha256(cache_path), from_cache=True)
    raise DownloadError(f"download failed for {url}: {last_error}") from last_error
