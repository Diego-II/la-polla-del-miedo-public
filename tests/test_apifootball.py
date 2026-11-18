"""Offline tests for the cache-first API-Football client.

These tests NEVER hit the network: the cache dir is pointed at ``tmp_path``
and the HTTP-performing method is monkeypatched to explode if it is ever
called on a cache hit / budget-exceeded path.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from polla import apifootball
from polla.apifootball import ApiFootball, BudgetExceeded, cache_key, ttl_for


# --- a tripwire HTTP layer -------------------------------------------------

def _boom(*args, **kwargs):
    raise AssertionError("HTTP must NOT be called in this test")


def _client(tmp_path, **kw) -> ApiFootball:
    # api_key/base_url supplied so the client never reaches real secrets().
    return ApiFootball(cache_dir=tmp_path, api_key="TEST", base_url="https://example.test", **kw)


def _seed_cache(client: ApiFootball, endpoint: str, params: dict, payload: dict,
                fetched_at: datetime) -> None:
    path = client._cache_path(endpoint, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": fetched_at.isoformat(),
        "endpoint": endpoint,
        "params": {str(k): str(v) for k, v in params.items()},
        "payload": payload,
    }))


# --- cache-first behaviour -------------------------------------------------

def test_fresh_cache_hit_returns_without_http(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(client, "_http_get", _boom)

    payload = {"get": "leagues", "errors": [], "results": 1, "response": [{"id": 1}]}
    _seed_cache(client, "leagues", {"search": "world cup"}, payload,
                fetched_at=datetime.now(timezone.utc))

    # get() returns the response list; fetch() returns the whole envelope.
    assert client.get("leagues", search="world cup") == [{"id": 1}]
    assert client.fetch("leagues", search="world cup")["results"] == 1


def test_stale_cache_is_not_used(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(client, "_http_get", _boom)

    # odds TTL is 6h; seed something 7h old -> stale -> would need HTTP -> boom.
    old = datetime.now(timezone.utc) - timedelta(hours=7)
    _seed_cache(client, "odds", {"fixture": 42}, {"response": ["stale"]}, fetched_at=old)

    with pytest.raises(AssertionError, match="HTTP must NOT"):
        client.get("odds", fixture=42)


def test_param_order_is_stable_for_cache_key(tmp_path, monkeypatch):
    client = _client(tmp_path)
    monkeypatch.setattr(client, "_http_get", _boom)

    payload = {"response": [{"ok": True}]}
    _seed_cache(client, "fixtures", {"league": 1, "season": 2026}, payload,
                fetched_at=datetime.now(timezone.utc))

    # Different kwarg order must resolve to the SAME cache entry (no HTTP).
    assert client.get("fixtures", season=2026, league=1) == [{"ok": True}]


# --- budget guard ----------------------------------------------------------

def test_budget_guard_raises_when_at_limit(tmp_path, monkeypatch):
    client = _client(tmp_path, max_per_day=3)
    monkeypatch.setattr(client, "_http_get", _boom)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "_usage.json").write_text(json.dumps({today: 3}))

    assert client.remaining_today() == 0
    with pytest.raises(BudgetExceeded):
        client.fetch("teams", id=99)  # uncached + at limit -> refuse, no HTTP


def test_cache_hit_does_not_consume_budget(tmp_path, monkeypatch):
    client = _client(tmp_path, max_per_day=1)
    monkeypatch.setattr(client, "_http_get", _boom)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_path / "_usage.json").write_text(json.dumps({today: 1}))  # budget exhausted

    _seed_cache(client, "leagues", {"id": 1}, {"response": [{"id": 1}]},
                fetched_at=datetime.now(timezone.utc))

    # Even with the budget fully spent, a fresh cache hit still works.
    assert client.get("leagues", id=1) == [{"id": 1}]
    assert client.used_today() == 1  # unchanged


# --- live path is exercised WITHOUT real network via a fake session --------

class _FakeResponse:
    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeSession:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        return _FakeResponse(self.payload, self.headers)


def test_live_call_writes_cache_and_records_usage(tmp_path):
    payload = {"get": "leagues", "errors": [], "results": 1, "response": [{"league": {"id": 1}}]}
    session = _FakeSession(payload, headers={"x-ratelimit-requests-remaining": "87"})
    client = ApiFootball(cache_dir=tmp_path, api_key="TEST",
                         base_url="https://example.test", session=session)

    assert client.get("leagues", search="world cup") == [{"league": {"id": 1}}]
    assert session.calls == 1
    assert client.used_today() == 1

    # Provider remaining header captured into usage.
    usage = json.loads((tmp_path / "_usage.json").read_text())
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert usage[f"{today}__provider_remaining"] == 87

    # Second call is served from cache -> session NOT hit again.
    assert client.get("leagues", search="world cup") == [{"league": {"id": 1}}]
    assert session.calls == 1


def test_api_errors_field_raises(tmp_path):
    payload = {"get": "leagues", "errors": {"token": "invalid"}, "response": []}
    session = _FakeSession(payload)
    client = ApiFootball(cache_dir=tmp_path, api_key="TEST",
                         base_url="https://example.test", session=session)
    with pytest.raises(apifootball.ApiError):
        client.get("leagues")


# --- pure helpers ----------------------------------------------------------

def test_ttl_map_resolves_leading_segment():
    assert ttl_for("fixtures") == timedelta(days=7)
    assert ttl_for("odds/live") == timedelta(hours=6)
    assert ttl_for("something-unknown") == apifootball.DEFAULT_TTL


def test_cache_key_is_order_independent():
    assert cache_key("fixtures", {"league": 1, "season": 2026}) == \
        cache_key("fixtures", {"season": 2026, "league": 1})
    assert cache_key("fixtures", {"league": 1}) != cache_key("fixtures", {"league": 2})
