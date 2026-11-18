from __future__ import annotations

import numpy as np

from polla import poisson
from polla.config import Preferences
from polla.config import Override, Strategy
from polla.models import Fixture, Scoring
from polla.predict import best_pick, predict_fixture


def test_grid_is_a_distribution():
    grid = poisson.score_grid(1.6, 1.1)
    assert abs(grid.sum() - 1.0) < 1e-9
    assert (grid >= 0).all()
    p = poisson.outcome_probs(grid)
    assert abs(sum(p) - 1.0) < 1e-9


def test_elo_expectation_symmetry():
    assert abs(poisson.elo_expectation(0) - 0.5) < 1e-12
    assert poisson.elo_expectation(200) > 0.5 > poisson.elo_expectation(-200)


def test_calibration_matches_target_and_orders_lambdas():
    for we in (0.45, 0.5, 0.65, 0.8):
        lh, la = poisson.calibrate_lambdas(we)
        got = poisson.expected_home_score(poisson.score_grid(lh, la))
        assert abs(got - we) < 1e-3
    lh, la = poisson.calibrate_lambdas(0.5)
    assert abs(lh - la) < 1e-3            # even match -> symmetric
    lh, la = poisson.calibrate_lambdas(0.8)
    assert lh > la                         # favorite scores more


def test_scoring_points_additive():
    s = Scoring()                          # group: outcome 1, exact 3 (additive)
    assert s.points((2, 1), (2, 1)) == 4   # exact -> outcome + exact
    assert s.points((2, 1), (3, 0)) == 1   # right outcome only
    assert s.points((2, 1), (0, 1)) == 0   # wrong
    from polla import scoring
    assert scoring.for_stage("FIN").points((1, 0), (1, 0)) == 50  # 20 + 30


def test_best_pick_favours_favourite_under_3_1_0():
    grid = poisson.score_grid(*poisson.calibrate_lambdas(0.8))
    i, j = best_pick(grid, Scoring(), "safe")
    assert i > j                           # safe pick backs the favourite


def _ev(grid, pick, scoring=None):
    scoring = scoring or Scoring()
    return sum(
        grid[a, b] * scoring.points(pick, (a, b))
        for a in range(grid.shape[0])
        for b in range(grid.shape[1])
    )


def test_best_pick_realism_zero_is_pure_ev():
    # realism=0 must be byte-for-byte the old behaviour (no surprise pick changes)
    for we in (0.5, 0.62, 0.8):
        grid = poisson.score_grid(*poisson.calibrate_lambdas(we))
        assert best_pick(grid, Scoring(), "safe", realism=0.0) == best_pick(grid, Scoring(), "safe")


def test_best_pick_realism_prefers_both_score_when_near_optimal():
    # near coin-flip: pure EV picks a clean sheet, realism surfaces a both-score line
    grid = poisson.score_grid(*poisson.calibrate_lambdas(0.55))
    safe = best_pick(grid, Scoring(), "safe")
    assert safe[0] == 0 or safe[1] == 0                       # pure EV -> clean sheet
    real = best_pick(grid, Scoring(), "safe", realism=0.12)
    assert real[0] > 0 and real[1] > 0                        # realism -> both teams score


def test_best_pick_realism_respects_ev_band():
    # the realism pick may never drop below (1-realism) * best_ev
    grid = poisson.score_grid(*poisson.calibrate_lambdas(0.55))
    best_ev = _ev(grid, best_pick(grid, Scoring(), "safe"))
    for r in (0.05, 0.10, 0.2):
        pick = best_pick(grid, Scoring(), "safe", realism=r)
        assert _ev(grid, pick) >= best_ev * (1 - r) - 1e-9


def test_predict_fixture_and_override():
    fx = Fixture(group="A", home="Mexico", away="South Africa", date="2026-06-11")
    p = predict_fixture(fx)
    assert p.p_home > p.p_away             # Mexico (host) favoured
    assert "-" in p.pick

    prefs = Preferences(strategy=Strategy(), overrides=[Override(match="Mexico vs South Africa", score="3-0")])
    p2 = predict_fixture(fx, prefs=prefs)
    assert p2.pick == "3-0" and p2.overridden


def test_toqui_team_name_aliases_resolve():
    from polla.ratings import DEFAULT_ELO, rating
    # Toqui/sportmonks name variants must map to the real squad's Elo, not 1600
    for variant, canonical in [
        ("Korea Republic", "South Korea"),
        ("Côte d'Ivoire", "Ivory Coast"),
        ("Cape Verde Islands", "Cape Verde"),
        ("Congo DR", "DR Congo"),
        ("Curacao", "Curaçao"),
    ]:
        assert rating(variant) == rating(canonical) != DEFAULT_ELO, variant
