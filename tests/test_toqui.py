"""Unit tests for the Toqui PARSING logic only — no live network.

These feed sample RSC-flight HTML (a tiny synthetic, deterministic fixture) into the pure parse functions and assert the
ToquiFixture / ToquiStanding values come out right.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Another test module (test_jobs.py) registers a *fake* ``polla.toqui`` in
# sys.modules at import time. If it was collected first, evict that stub so we
# import the real implementation from src/ for these parsing tests.
_existing = sys.modules.get("polla.toqui")
if _existing is not None and not hasattr(_existing, "decode_flight"):
    del sys.modules["polla.toqui"]
    importlib.invalidate_caches()

from polla.toqui import (
    ToquiFixture,
    ToquiStanding,
    _is_placeholder_team,
    _pick_from_prediction,
    decode_flight,
    game_to_fixture,
    is_locked,
    parse_fixtures,
    parse_games_flight,
    parse_standings,
    parse_standings_flight,
    standing_to_obj,
)

SAMPLES = Path(__file__).parent / "_samples"
PREDICT_HTML = (SAMPLES / "predict_sample.html").read_text()
STANDINGS_HTML = (SAMPLES / "standings_sample.html").read_text()


# --------------------------------------------------------------------------- #
# Flight decoding
# --------------------------------------------------------------------------- #
def test_decode_flight_recovers_inner_json():
    flight = decode_flight(PREDICT_HTML)
    assert '"games":' in flight
    assert "Mexico" in flight
    assert "self.__next_f" not in flight  # we unwrapped the JS envelope


def test_decode_flight_handles_no_flight():
    assert decode_flight("<html><body>nothing</body></html>") == ""


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def test_parse_games_flight_count_and_keys():
    games = parse_games_flight(PREDICT_HTML)
    assert len(games) == 3
    assert {g["id"] for g in games} == {"g_group_1", "g_group_2", "g_final"}


def test_parse_fixtures_returns_dataclasses():
    fixtures = parse_fixtures(PREDICT_HTML)
    assert len(fixtures) == 3
    assert all(isinstance(f, ToquiFixture) for f in fixtures)


def test_fixture_field_mapping():
    fixtures = {f.match_id: f for f in parse_fixtures(PREDICT_HTML)}
    mex = fixtures["g_group_1"]
    assert mex.home == "Mexico"
    assert mex.away == "South Africa"
    assert mex.stage == "group"
    assert mex.kickoff_utc == "2026-06-11T19:00:00.000Z"
    assert mex.lock_utc == "2026-06-11T19:00:00.000Z"
    assert mex.my_pick is None


def test_fixture_existing_pick_is_parsed_as_tuple():
    fixtures = {f.match_id: f for f in parse_fixtures(PREDICT_HTML)}
    kor = fixtures["g_group_2"]
    assert kor.my_pick == (2, 1)
    assert isinstance(kor.my_pick, tuple)


def test_group_stage_locks_all_at_once_not_per_match():
    """Both group games kick off at different times but share ONE lock time —
    the group stage locks all-at-once at the first kickoff, not per match."""
    fixtures = {f.match_id: f for f in parse_fixtures(PREDICT_HTML)}
    g1, g2 = fixtures["g_group_1"], fixtures["g_group_2"]
    assert g1.kickoff_utc != g2.kickoff_utc          # different kickoffs
    assert g1.lock_utc == g2.lock_utc                # same lock instant
    assert g1.lock_utc == "2026-06-11T19:00:00.000Z"


def test_placeholder_knockout_fixture_parsed():
    fixtures = {f.match_id: f for f in parse_fixtures(PREDICT_HTML)}
    fin = fixtures["g_final"]
    assert fin.stage == "final"
    assert fin.home == "Winner Semi-final 1"
    assert fin.my_pick is None


def test_game_to_fixture_pick_with_only_one_score_is_none():
    g = {"id": "x", "stage": "group", "homeTeam": "A", "awayTeam": "B",
         "kickoffAt": None, "locksAt": None,
         "prediction": {"predictedHomeScore": 1, "predictedAwayScore": None}}
    assert game_to_fixture(g).my_pick is None


def test_game_to_fixture_zero_zero_pick_is_kept():
    g = {"id": "x", "stage": "group", "homeTeam": "A", "awayTeam": "B",
         "kickoffAt": None, "locksAt": None,
         "prediction": {"predictedHomeScore": 0, "predictedAwayScore": 0}}
    assert game_to_fixture(g).my_pick == (0, 0)


# --------------------------------------------------------------------------- #
# Prediction-shape tolerance — the original "0 picks persisted" bug.
# --------------------------------------------------------------------------- #
# The live predict-page flight nests a saved pick as
#   {"homeScore": H, "awayScore": A, "locked": false}
# NOT {"predictedHomeScore": H, ...}. Reading the wrong key made every saved
# pick read back as None, which looked like "nothing persisted". The reader must
# accept BOTH shapes.
def test_pick_from_prediction_predict_page_flight_shape():
    assert _pick_from_prediction(
        {"homeScore": 2, "awayScore": 0, "locked": False}) == (2, 0)


def test_pick_from_prediction_rest_shape():
    assert _pick_from_prediction(
        {"predictedHomeScore": 1, "predictedAwayScore": 3}) == (1, 3)


def test_pick_from_prediction_zero_zero_flight_shape_kept():
    assert _pick_from_prediction(
        {"homeScore": 0, "awayScore": 0, "locked": False}) == (0, 0)


@pytest.mark.parametrize("pred", [
    None, {}, {"homeScore": 1}, {"awayScore": 2},
    {"homeScore": 1, "awayScore": None}, "not-a-dict",
])
def test_pick_from_prediction_incomplete_is_none(pred):
    assert _pick_from_prediction(pred) is None


def test_game_to_fixture_reads_live_predict_flight_shape():
    """REGRESSION: the live /predict flight uses prediction.homeScore — a saved
    pick must be visible through game_to_fixture (it was not, which is why a
    successful 36-pick write read back as 0)."""
    g = {"id": "g", "stage": "group", "homeTeam": "Mexico",
         "awayTeam": "South Africa", "kickoffAt": None, "locksAt": None,
         "prediction": {"homeScore": 3, "awayScore": 1, "locked": False}}
    assert game_to_fixture(g).my_pick == (3, 1)


# --------------------------------------------------------------------------- #
# Standings
# --------------------------------------------------------------------------- #
def test_parse_standings_flight_count():
    raw = parse_standings_flight(STANDINGS_HTML)
    assert len(raw) == 3


def test_parse_standings_returns_dataclasses():
    standings = parse_standings(STANDINGS_HTML)
    assert len(standings) == 3
    assert all(isinstance(s, ToquiStanding) for s in standings)


def test_standing_field_mapping_and_order():
    standings = parse_standings(STANDINGS_HTML)
    assert [(s.rank, s.name, s.points) for s in standings] == [
        (1, "Player 1", 12),
        (2, "Player 2", 7),
        (3, "Player 3", 3),
    ]


def test_standing_to_obj_prefers_display_name():
    s = standing_to_obj({"rank": 5, "points": 9,
                         "user": {"displayName": "Nick", "name": "Real"}})
    assert s == ToquiStanding(rank=5, name="Nick", points=9)


def test_standing_to_obj_falls_back_to_name():
    s = standing_to_obj({"rank": 2, "points": 4, "user": {"name": "OnlyName"}})
    assert s.name == "OnlyName"


# --------------------------------------------------------------------------- #
# Lock helper
# --------------------------------------------------------------------------- #
def test_is_locked_before_and_after():
    fx = ToquiFixture("id", "group", "A", "B",
                      kickoff_utc="2026-06-11T19:00:00.000Z",
                      lock_utc="2026-06-11T19:00:00.000Z", my_pick=None)
    before = datetime(2026, 6, 10, tzinfo=timezone.utc)
    after = datetime(2026, 6, 12, tzinfo=timezone.utc)
    assert is_locked(fx, now=before) is False
    assert is_locked(fx, now=after) is True


def test_is_locked_none_lock_is_unlocked():
    fx = ToquiFixture("id", "group", "A", "B", None, None, None)
    assert is_locked(fx) is False


# --------------------------------------------------------------------------- #
# Placeholder-team detection (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "Winner Semi-final 1", "Loser Semi-final 2", "Runner-up Group C",
    "2nd position Group A", "1st position Group F", None, "",
])
def test_placeholder_team_detection_positive(name):
    assert _is_placeholder_team(name) is True


@pytest.mark.parametrize("name", ["Mexico", "South Africa", "Korea Republic",
                                  "Czech Republic", "Bosnia and Herzegovina"])
def test_placeholder_team_detection_negative(name):
    assert _is_placeholder_team(name) is False
