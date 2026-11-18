"""Cache-first client for the API-Football (api-sports.io) v3 API.

Design goals (the free tier is ~100 live requests/day and the key is REAL):

  * CACHE-FIRST. Every live response is written to disk. A fresh cached entry
    is returned WITHOUT any HTTP call, so re-running scripts costs nothing.
  * Frugal. A per-calendar-day budget guard refuses to exceed ``max_per_day``
    live requests and raises :class:`BudgetExceeded` instead.
  * Typed + injectable. The cache directory and the budget are constructor
    arguments so tests can point them at ``tmp_path`` and never touch the wire.

Cache layout::

    data_cache/apifootball/<endpoint>/<hash>.json   # one file per (endpoint, params)
    data_cache/apifootball/_usage.json              # {"2026-06-04": 3, ...}

Each cache file stores ``{fetched_at, endpoint, params, payload}`` where
``payload`` is the full raw API envelope
(``{get, parameters, errors, results, paging, response}``).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .config import ROOT, secrets

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Default on-disk root for the API-Football cache.
DEFAULT_CACHE_DIR = ROOT / "data_cache" / "apifootball"

#: Default daily live-request budget (free tier is ~100/day; leave headroom).
DEFAULT_MAX_PER_DAY = 90

#: Time-to-live per endpoint. Edit freely. Keys are matched against the leading
#: path segment of the endpoint (e.g. "odds" matches "odds" and "odds/live").
#: Schedule-like data barely moves -> long TTL. Volatile data -> short TTL.
TTL: dict[str, timedelta] = {
    "leagues": timedelta(days=7),
    "teams": timedelta(days=7),
    "fixtures": timedelta(days=7),     # the WC2026 schedule is fixed
    "standings": timedelta(days=1),
    "odds": timedelta(hours=6),
    "injuries": timedelta(hours=6),
    "predictions": timedelta(hours=6),
    "lineups": timedelta(hours=6),
}

#: Fallback TTL when the endpoint has no entry in :data:`TTL`.
DEFAULT_TTL = timedelta(hours=24)

USAGE_FILENAME = "_usage.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ApiFootballError(RuntimeError):
    """Base error for this client."""


class BudgetExceeded(ApiFootballError):
    """Raised when a LIVE request would exceed the daily budget.

    No HTTP call is made when this is raised.
    """


class ApiError(ApiFootballError):
    """Raised when the API envelope reports a non-empty ``errors`` field."""


# ---------------------------------------------------------------------------
# Helpers (module-level so they are easy to unit-test)
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_key(now: datetime | None = None) -> str:
    return (now or _utcnow()).strftime("%Y-%m-%d")


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values and stringify everything, for a stable cache key."""
    return {str(k): str(v) for k, v in sorted(params.items()) if v is not None}


def _slug(endpoint: str) -> str:
    """A filesystem-safe slug for the endpoint path (``odds/live`` -> ``odds_live``)."""
    return re.sub(r"[^a-z0-9]+", "_", endpoint.strip("/").lower()).strip("_") or "root"


def cache_key(endpoint: str, params: dict[str, Any]) -> str:
    """Stable hash of the endpoint plus sorted, normalized params."""
    norm = _normalize_params(params)
    blob = json.dumps([endpoint.strip("/").lower(), norm], sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def ttl_for(endpoint: str) -> timedelta:
    """TTL for an endpoint, matched on its leading path segment."""
    head = endpoint.strip("/").split("/", 1)[0].lower()
    return TTL.get(head, DEFAULT_TTL)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ApiFootball:
    """A cache-first, budget-guarded API-Football client.

    Args:
        cache_dir: root directory for cached responses. Injectable for tests.
        max_per_day: maximum LIVE requests allowed per calendar day.
        api_key / base_url: override credentials (default: from ``secrets()``).
        session: a ``requests.Session``-like object (injectable for tests).
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        *,
        max_per_day: int = DEFAULT_MAX_PER_DAY,
        api_key: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self.max_per_day = int(max_per_day)
        self._api_key = api_key
        self._base_url = base_url
        self._session = session

    # -- credentials (lazy so tests never need a real key) -----------------

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = secrets().api_football_key or ""
        return self._api_key

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = secrets().api_football_url
        return self._base_url.rstrip("/")

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    # -- paths -------------------------------------------------------------

    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        return self.cache_dir / _slug(endpoint) / f"{cache_key(endpoint, params)}.json"

    @property
    def _usage_path(self) -> Path:
        return self.cache_dir / USAGE_FILENAME

    # -- usage tracking ----------------------------------------------------

    def _read_usage(self) -> dict[str, int]:
        if self._usage_path.exists():
            try:
                data = json.loads(self._usage_path.read_text())
                if isinstance(data, dict):
                    return {str(k): int(v) for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                pass
        return {}

    def _write_usage(self, usage: dict[str, int]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._usage_path.write_text(json.dumps(usage, indent=2, sort_keys=True))

    def used_today(self, now: datetime | None = None) -> int:
        """LIVE requests already spent on the current calendar day (UTC)."""
        return self._read_usage().get(_today_key(now), 0)

    def remaining_today(self, now: datetime | None = None) -> int:
        """Budget left for live calls today (never negative)."""
        return max(0, self.max_per_day - self.used_today(now))

    def _record_live_call(self, remaining_header: str | None, now: datetime) -> None:
        usage = self._read_usage()
        key = _today_key(now)
        usage[key] = usage.get(key, 0) + 1
        # If the API tells us how many requests remain on its side, capture it
        # so the usage file reflects the provider's view too.
        if remaining_header is not None:
            try:
                usage[f"{key}__provider_remaining"] = int(remaining_header)
            except (TypeError, ValueError):
                pass
        self._write_usage(usage)

    # -- cache I/O ---------------------------------------------------------

    def _read_cache(self, path: Path, ttl: timedelta, now: datetime) -> dict | None:
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text())
            fetched = datetime.fromisoformat(entry["fetched_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if now - fetched > ttl:
            return None  # stale
        return entry

    def _write_cache(
        self, path: Path, endpoint: str, params: dict[str, Any],
        payload: dict, now: datetime,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "fetched_at": now.isoformat(),
            "endpoint": endpoint,
            "params": _normalize_params(params),
            "payload": payload,
        }
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False))

    # -- HTTP (the ONLY place that touches the network) --------------------

    def _http_get(self, endpoint: str, params: dict[str, Any], now: datetime) -> dict:
        """Perform a LIVE request. Monkeypatch this in tests to assert no I/O."""
        url = f"{self.base_url}/{endpoint.strip('/')}"
        resp = self.session.get(
            url,
            params=_normalize_params(params),
            headers={"x-apisports-key": self.api_key},
            timeout=30,
        )
        # Count the call the moment it is made (success or not), and capture the
        # provider's remaining-quota header if present.
        self._record_live_call(
            resp.headers.get("x-ratelimit-requests-remaining"), now,
        )
        resp.raise_for_status()
        payload = resp.json()
        errors = payload.get("errors")
        if errors:  # API-Football puts auth/quota/param errors here, not in HTTP
            raise ApiError(f"API error for {endpoint}: {errors}")
        return payload

    # -- public API --------------------------------------------------------

    def fetch(self, endpoint: str, *, refresh: bool = False, **params: Any) -> dict:
        """Return the full raw API envelope for ``endpoint`` (cache-first).

        Args:
            endpoint: e.g. ``"leagues"``, ``"fixtures"``, ``"odds"``.
            refresh: if True, ignore a fresh cache entry and force a live call
                (still subject to the daily budget).
            **params: query parameters (``None`` values are dropped).

        Returns the dict ``{get, parameters, errors, results, paging, response}``.
        """
        now = _utcnow()
        path = self._cache_path(endpoint, params)

        if not refresh:
            cached = self._read_cache(path, ttl_for(endpoint), now)
            if cached is not None:
                return cached["payload"]  # CACHE HIT -> zero HTTP, zero budget

        # Budget guard BEFORE any network I/O.
        if self.used_today(now) >= self.max_per_day:
            raise BudgetExceeded(
                f"daily budget {self.max_per_day} reached "
                f"({self.used_today(now)} live calls on {_today_key(now)}); "
                f"refusing live call to {endpoint}"
            )

        payload = self._http_get(endpoint, params, now)
        self._write_cache(path, endpoint, params, payload, now)
        return payload

    def get(self, endpoint: str, *, refresh: bool = False, **params: Any) -> list:
        """Cache-first fetch returning just the ``response`` list payload."""
        return self.fetch(endpoint, refresh=refresh, **params).get("response", [])
