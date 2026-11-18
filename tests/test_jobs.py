"""Tests for the jobs layer.

Everything is mocked: no real AWS, no real Toqui, no network.

  * `polla.toqui` may not exist yet at test time,
    so we inject a fake module into sys.modules *before* importing
    jobs.pre_lock_submit. The fake mirrors the documented contract exactly.
  * The store functions (DynamoDB) are monkeypatched on the jobs module so the
    boto3 resource is never created.

Assertions cover the three things that must never regress:
  1. a preferences override beats the model pick,
  2. a `skip` entry is never submitted,
  3. the job stays dry-run unless --live is passed.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

# --------------------------------------------------------------------------
# Fake `polla.toqui` module — matches the agreed contract:
#   ToquiClient(headless=True) context manager:
#       .login(), .fixtures()->[ToquiFixture], .standings()->[ToquiStanding],
#       .submit_pick(match_id, home_goals, away_goals, *, dry_run=True)->bool
#   ToquiFixture(match_id, stage, home, away, kickoff_utc, lock_utc, my_pick)
# --------------------------------------------------------------------------


@dataclass
class ToquiFixture:
    match_id: str
    stage: str
    home: str
    away: str
    kickoff_utc: datetime
    lock_utc: datetime
    my_pick: str | None = None
    # result fields (same contract as the real dataclass)
    status: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    points: int | None = None
    group: str | None = None
    is_placeholder: bool = False


@dataclass
class ToquiStanding:
    rank: int
    user: str
    points: int


@dataclass
class _SubmitCall:
    match_id: str
    home_goals: int
    away_goals: int
    dry_run: bool


class FakeToquiClient:
    """Records calls; never touches a browser or the network."""

    # populated per-test before the job runs
    fixtures_to_return: list[ToquiFixture] = []
    submit_calls: list[_SubmitCall] = []
    logged_in: bool = False

    def __init__(self, headless: bool = True):
        self.headless = headless

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self):
        type(self).logged_in = True

    def fixtures(self):
        return list(type(self).fixtures_to_return)

    def standings(self):
        return []

    def submit_pick(self, match_id, home_goals, away_goals, *, dry_run=True, **_kw):
        type(self).submit_calls.append(
            _SubmitCall(match_id, home_goals, away_goals, dry_run)
        )
        return True


def _install_fake_toqui():
    mod = types.ModuleType("polla.toqui")
    mod.ToquiClient = FakeToquiClient
    mod.ToquiFixture = ToquiFixture
    mod.ToquiStanding = ToquiStanding
    sys.modules["polla.toqui"] = mod


_install_fake_toqui()

# Import AFTER the fake module is registered.
from jobs import pre_lock_submit  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeToquiClient.fixtures_to_return = []
    FakeToquiClient.submit_calls = []
    FakeToquiClient.logged_in = False
    yield


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    """Never hit DynamoDB: capture submission logs in a list instead."""
    logged: list[tuple] = []
    monkeypatch.setattr(
        pre_lock_submit, "log_submission",
        lambda match_id, score, ts: logged.append((match_id, score, ts)),
    )
    return logged


def _fixture(match_id, home, away, *, lock_in_hours=2.0, my_pick=None, stage="GRP"):
    now = datetime.now(timezone.utc)
    return ToquiFixture(
        match_id=match_id,
        stage=stage,
        home=home,
        away=away,
        kickoff_utc=now + timedelta(hours=lock_in_hours + 1),
        lock_utc=now + timedelta(hours=lock_in_hours),
        my_pick=my_pick,
    )


def _prefs(monkeypatch, *, overrides=None, skip=None):
    """Build a real Preferences object and force the job to use it."""
    from polla.config import Override, Preferences, Strategy

    prefs = Preferences(
        strategy=Strategy(),
        interesting_teams=[],
        overrides=[Override(**o) for o in (overrides or [])],
        skip=skip or [],
    )
    monkeypatch.setattr(pre_lock_submit, "preferences", lambda: prefs)
    return prefs


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_override_beats_model(monkeypatch):
    """When preferences pin a score, it is submitted verbatim, not the model's."""
    fx = _fixture("m1", "Spain", "Uruguay")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch, overrides=[{"match": "Spain vs Uruguay", "score": "3-2"}])

    # Make the model return something clearly different so a pass can't be luck.
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="0-0"),
    )

    rc = pre_lock_submit.run(within_hours=24, live=True)
    assert rc == 0

    assert len(FakeToquiClient.submit_calls) == 1
    call = FakeToquiClient.submit_calls[0]
    assert (call.home_goals, call.away_goals) == (3, 2)  # override, not 0-0
    assert call.dry_run is False  # live=True


def test_skip_is_never_submitted(monkeypatch):
    """A match listed in `skip` is never submitted, even though it locks soon."""
    keep = _fixture("keep", "France", "Germany")
    skip = _fixture("skip", "England", "Portugal")
    FakeToquiClient.fixtures_to_return = [keep, skip]
    _prefs(monkeypatch, skip=["England vs Portugal"])

    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="1-0"),
    )

    pre_lock_submit.run(within_hours=24, live=True)

    submitted_ids = {c.match_id for c in FakeToquiClient.submit_calls}
    assert submitted_ids == {"keep"}
    assert "skip" not in submitted_ids


def test_dry_run_by_default(monkeypatch):
    """Without --live, submit_pick is called with dry_run=True."""
    fx = _fixture("m1", "France", "Germany")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.run(within_hours=24, live=False)

    assert len(FakeToquiClient.submit_calls) == 1
    assert FakeToquiClient.submit_calls[0].dry_run is True


def test_cli_default_is_dry_run(monkeypatch):
    """The argparse path defaults to dry-run when --live is absent."""
    fx = _fixture("m1", "France", "Germany")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.main(["--within-hours", "24"])
    assert FakeToquiClient.submit_calls[0].dry_run is True

    FakeToquiClient.submit_calls = []
    pre_lock_submit.main(["--within-hours", "24", "--live"])
    assert FakeToquiClient.submit_calls[0].dry_run is False


def test_already_picked_is_skipped(monkeypatch):
    """Fixtures with an existing pick are left alone."""
    fx = _fixture("m1", "France", "Germany", my_pick="1-1")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.run(within_hours=24, live=True)
    assert FakeToquiClient.submit_calls == []


def test_outside_window_is_skipped(monkeypatch):
    """A match locking far in the future is not submitted with a tight window."""
    fx = _fixture("m1", "France", "Germany", lock_in_hours=48)
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.run(within_hours=24, live=True)
    assert FakeToquiClient.submit_calls == []


def test_already_locked_is_skipped(monkeypatch):
    """A match whose lock is in the past is out of window."""
    fx = _fixture("m1", "France", "Germany", lock_in_hours=-1)
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.run(within_hours=24, live=True)
    assert FakeToquiClient.submit_calls == []


def test_submission_logged_to_store(monkeypatch, _stub_store):
    """A real (live) submit writes one audit row to the store."""
    fx = _fixture("m1", "France", "Germany")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.run(within_hours=24, live=True)
    assert len(_stub_store) == 1
    match_id, score, _ts = _stub_store[0]
    assert match_id == "m1"
    assert score == "2-1"  # live submissions are logged without the dry-run tag


def test_dry_run_log_is_tagged(monkeypatch, _stub_store):
    """Dry-run submissions are logged but tagged, for an intent audit trail."""
    fx = _fixture("m1", "France", "Germany")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch)
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="2-1"),
    )

    pre_lock_submit.run(within_hours=24, live=False)
    assert len(_stub_store) == 1
    _match_id, score, _ts = _stub_store[0]
    assert "dry-run" in score


def test_override_by_match_id(monkeypatch):
    """Override keyed by toqui match_id is honored."""
    fx = _fixture("xyz789", "Brazil", "Argentina")
    FakeToquiClient.fixtures_to_return = [fx]
    _prefs(monkeypatch, overrides=[{"match_id": "xyz789", "score": "4-0"}])
    monkeypatch.setattr(
        pre_lock_submit, "predict_fixture",
        lambda fixture, scoring=None: types.SimpleNamespace(pick="1-1"),
    )

    pre_lock_submit.run(within_hours=24, live=True)
    call = FakeToquiClient.submit_calls[0]
    assert (call.home_goals, call.away_goals) == (4, 0)
