"""Unit tests for polla.results — pure logic, no network/browser."""
from __future__ import annotations

from datetime import datetime, timezone

import yaml

from polla.results import (
    MatchResult,
    finished_results,
    render_adjustments_yaml,
    render_results_md,
    suggested_adjustment,
    team_form,
)
from polla.toqui import ToquiFixture

NOW = datetime(2026, 6, 12, 13, 0, tzinfo=timezone.utc)


def _fx(home="Mexico", away="South Africa", status="finished", hs=2, as_=0,
        pick=(2, 0), points=4, stage="group", group="A"):
    return ToquiFixture(
        match_id="m1", stage=stage, home=home, away=away,
        kickoff_utc=None, lock_utc=None, my_pick=pick,
        status=status, home_score=hs, away_score=as_, points=points, group=group)


# --------------------------------------------------------------------------- #
# finished_results
# --------------------------------------------------------------------------- #
def test_only_finished_games_with_scores_are_included():
    fixtures = [
        _fx(),                                        # finished -> in
        _fx(status="scheduled", hs=None, as_=None),   # not played -> out
        _fx(status="live", hs=1, as_=0),              # in progress -> out
        _fx(status="finished", hs=None, as_=None),    # finished but unscored -> out
    ]
    results = finished_results(fixtures)
    assert len(results) == 1
    assert results[0].actual == "2-0"
    assert results[0].pick == "2-0"


def test_toqui_points_are_trusted_when_present():
    assert finished_results([_fx(points=4)])[0].points == 4


def test_points_computed_from_scoring_when_toqui_has_not_graded():
    # group: outcome 1 + exact 3
    assert finished_results([_fx(points=None)])[0].points == 4
    # outcome only (2-0 picked, 1-0 actual): 1 point
    assert finished_results([_fx(points=None, hs=1, as_=0)])[0].points == 1
    # R32 weights: exact = 3 + 5 = 8
    assert finished_results(
        [_fx(points=None, stage="round-of-32", group=None)])[0].points == 8
    # no pick -> 0
    assert finished_results([_fx(points=None, pick=None)])[0].points == 0


def test_hit_classification():
    r = finished_results([_fx()])[0]
    assert r.hit == "exact"
    assert finished_results([_fx(hs=3, as_=1)])[0].hit == "outcome"   # picked 2-0
    assert finished_results([_fx(hs=0, as_=0)])[0].hit == "miss"
    assert finished_results([_fx(pick=None)])[0].hit == "none"


# --------------------------------------------------------------------------- #
# team form + adjustments
# --------------------------------------------------------------------------- #
def test_team_form_surprise_is_symmetric():
    # Expectation injected as 0 (even match): a 3-0 means +3 surprise for the
    # winner and -3 for the loser, per game.
    results = finished_results([_fx(hs=3, as_=0)])
    forms = {f.team: f for f in team_form(results, expected_gd_fn=lambda h, a: 0.0)}
    assert forms["Mexico"].surprise_pg == 3.0
    assert forms["South Africa"].surprise_pg == -3.0
    assert forms["Mexico"].adjustment == -forms["South Africa"].adjustment > 0


def test_team_form_uses_canonical_names_and_skips_knockouts():
    results = finished_results([
        _fx(home="Korea Republic", away="Czech Republic", hs=2, as_=1),
        _fx(stage="round-of-32", group=None, hs=5, as_=0),  # not group -> ignored
    ])
    forms = team_form(results, expected_gd_fn=lambda h, a: 0.0)
    assert {f.team for f in forms} == {"South Korea", "Czech Republic"}


def test_adjustment_shrinkage_and_cap():
    # 1 game counts half: 25 * 1.0 * 1/2 = 12.5 -> 12
    assert suggested_adjustment(1.0, 1) == 12
    # more games, more weight: 25 * 1.0 * 3/4 = 18.75 -> 19
    assert suggested_adjustment(1.0, 3) == 19
    # capped at +/-60 even for absurd surprises
    assert suggested_adjustment(9.0, 3) == 60
    assert suggested_adjustment(-9.0, 3) == -60


# --------------------------------------------------------------------------- #
# renderers
# --------------------------------------------------------------------------- #
def test_results_markdown_totals_and_rows():
    results = finished_results([_fx(), _fx(home="Korea Republic",
                                           away="Czech Republic", hs=2, as_=1,
                                           pick=(2, 1))])
    forms = team_form(results, expected_gd_fn=lambda h, a: 0.0)
    md = render_results_md(results, forms, NOW)
    assert "**Points so far: 8**" in md
    assert "2 finished" in md
    assert "| group | A | Mexico vs South Africa | **2-0** | 2-0 | ✅ exact | 4 |" in md
    assert "South Korea" in md  # canonicalized in the form table


def test_adjustments_yaml_round_trips_and_keeps_enabled_flag():
    results = finished_results([_fx(hs=3, as_=0)])
    forms = team_form(results, expected_gd_fn=lambda h, a: 0.0)
    text = render_adjustments_yaml(forms, NOW, enabled=False)
    doc = yaml.safe_load(text)
    assert doc["enabled"] is False
    assert doc["adjustments"]["Mexico"] > 0
    assert doc["adjustments"]["South Africa"] < 0
    assert "Mexico" in doc["notes"]


def test_adjustments_yaml_empty_when_no_corrections():
    doc = yaml.safe_load(render_adjustments_yaml([], NOW))
    assert doc["enabled"] is True
    assert doc["adjustments"] == {}


def test_match_result_outcome_requires_matching_sign():
    # a draw pick on a decisive result is a miss, not an outcome hit
    r = MatchResult(stage="group", home="A", away="B",
                    home_score=1, away_score=0, pick="1-1")
    assert r.hit == "miss"


# --------------------------------------------------------------------------- #
# stage map contract
# --------------------------------------------------------------------------- #
def test_every_toqui_stage_maps_to_a_real_scoring_stage():
    from polla import scoring
    # Stage strings as emitted by the Toqui flight (captured 2026-06-12).
    toqui_stages = {"group", "round-of-32", "round-of-16", "quarter-final",
                    "semi-final", "third-place", "final"}
    assert set(scoring.TOQUI_STAGES) == toqui_stages
    for stage in toqui_stages:
        s = scoring.for_toqui_stage(stage)
        assert s.outcome > 0 and s.exact > 0
    # knockout stages must pay MORE than group
    assert scoring.for_toqui_stage("final").exact > scoring.for_toqui_stage("group").exact
