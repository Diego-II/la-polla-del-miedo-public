"""Contract tests for the generated national-team ratings file.

These tests assert the *written* ``data/ratings.yaml`` keeps the schema the
prediction model depends on, covers all 48 World Cup 2026 squads, and carries
numeric values on a sane Elo-like scale. They never touch the network: they
read the committed file produced by ``scripts/refresh_ratings.py``.
"""
from __future__ import annotations

import yaml

from polla.config import DATA
from polla.ratings import rating

# The 48 squad names are the contract: ``polla.ratings`` matches fixtures by
# these exact keys, so they must never drift.
EXPECTED_TEAMS = {
    "Mexico", "South Africa", "South Korea", "Czech Republic",
    "Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland",
    "Brazil", "Morocco", "Haiti", "Scotland",
    "United States", "Paraguay", "Australia", "Turkey",
    "Germany", "Ecuador", "Ivory Coast", "Curaçao",
    "Netherlands", "Japan", "Sweden", "Tunisia",
    "Belgium", "Egypt", "Iran", "New Zealand",
    "Spain", "Uruguay", "Cape Verde", "Saudi Arabia",
    "France", "Senegal", "Norway", "Iraq",
    "Argentina", "Algeria", "Austria", "Jordan",
    "Portugal", "Colombia", "DR Congo", "Uzbekistan",
    "England", "Croatia", "Ghana", "Panama",
}

RATING_MIN = 1300.0
RATING_MAX = 2400.0  # public mirror: raised from 2200; the final post-tournament Elo snapshot has Spain above it


def _load() -> dict:
    return yaml.safe_load((DATA / "ratings.yaml").read_text())


def test_top_level_schema_keys_present():
    data = _load()
    for key in ("as_of", "source", "home_advantage_elo", "host_nations", "ratings"):
        assert key in data, f"missing top-level key {key!r}"
    assert isinstance(data["ratings"], dict)


def test_host_nations_and_home_advantage_preserved():
    data = _load()
    assert data["host_nations"] == ["Mexico", "United States", "Canada"]
    assert isinstance(data["home_advantage_elo"], (int, float))
    assert data["home_advantage_elo"] > 0


def test_all_48_squads_present_and_no_extras():
    data = _load()
    keys = set(data["ratings"])
    assert keys == EXPECTED_TEAMS, (
        f"missing={EXPECTED_TEAMS - keys} extra={keys - EXPECTED_TEAMS}"
    )


def test_values_numeric_and_in_sane_range():
    data = _load()
    for team, value in data["ratings"].items():
        assert isinstance(value, (int, float)), f"{team} not numeric: {value!r}"
        assert RATING_MIN <= float(value) <= RATING_MAX, (
            f"{team} rating {value} outside [{RATING_MIN}, {RATING_MAX}]"
        )


def test_ratings_have_spread_not_all_equal():
    data = _load()
    values = [float(v) for v in data["ratings"].values()]
    assert max(values) - min(values) > 200, "ratings collapsed to a flat scale"


def test_relative_ordering_is_sound():
    # Sanity on the relative scale: elite teams clearly above minnows.
    data = _load()
    r = {k: float(v) for k, v in data["ratings"].items()}
    assert r["Spain"] > r["Haiti"]
    assert r["France"] > r["New Zealand"]
    assert r["Argentina"] > r["Qatar"]
    assert r["Brazil"] > r["Saudi Arabia"]


def test_model_loads_via_public_api():
    # The model reads through polla.ratings.rating(); it must resolve real keys
    # (not fall back to DEFAULT_ELO) for representative squads.
    for team in ("Spain", "Haiti", "France", "Argentina", "United States",
                 "New Zealand", "Czech Republic", "Curaçao"):
        value = rating(team)
        assert RATING_MIN <= value <= RATING_MAX, f"{team} -> {value}"


# --------------------------------------------------------------------------- #
# form adjustments (data/form_adjustments.yaml)
# --------------------------------------------------------------------------- #
def test_form_adjustments_applied_and_base_bypasses(tmp_path, monkeypatch):
    import polla.ratings as ratings
    f = tmp_path / "form_adjustments.yaml"
    f.write_text("enabled: true\nadjustments:\n  Spain: 40\n")
    monkeypatch.setattr(ratings, "FORM_ADJUSTMENTS", f)
    ratings._adjustments.cache_clear()
    try:
        assert ratings.rating("Spain") == ratings.rating("Spain", base=True) + 40
        # alias resolution: adjustment keyed by canonical name reaches aliases too
        assert ratings.rating("Korea Republic") == ratings.rating("South Korea")
    finally:
        ratings._adjustments.cache_clear()


def test_form_adjustments_disabled_flag(tmp_path, monkeypatch):
    import polla.ratings as ratings
    f = tmp_path / "form_adjustments.yaml"
    f.write_text("enabled: false\nadjustments:\n  Spain: 40\n")
    monkeypatch.setattr(ratings, "FORM_ADJUSTMENTS", f)
    ratings._adjustments.cache_clear()
    try:
        assert ratings.rating("Spain") == ratings.rating("Spain", base=True)
    finally:
        ratings._adjustments.cache_clear()


def test_canonical_names():
    from polla.ratings import canonical
    assert canonical("Korea Republic") == "South Korea"
    assert canonical("Turkiye") == "Turkey"
    assert canonical("Spain") == "Spain"
