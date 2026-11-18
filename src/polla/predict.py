from __future__ import annotations

import numpy as np

from . import poisson, ratings
from .config import Preferences, preferences
from .models import Fixture, Prediction, Scoring, ScorelineProb

MAX_PICK_GOALS = 5  # only consider 0..5 as a deliberate scoreline


def _parse_score(s: str) -> tuple[int, int]:
    h, a = s.replace(":", "-").split("-")
    return int(h), int(a)


def best_pick(
    grid: np.ndarray, scoring: Scoring, risk: str = "safe", realism: float = 0.0
) -> tuple[int, int]:
    """Scoreline maximizing expected points (safe) or hit probability (aggressive).

    Pure EV-max collapses every competitive match onto a clean sheet (1-0/2-0):
    a clean-sheet pick banks the outcome point on the *most* probable result (a
    win), while any both-teams-score line rides the least probable one (a draw).

    `realism` (0..1) buys both-teams-score lines for a sliver of EV: if any
    scoreline with both teams scoring sits within `realism` of the best EV, take
    the highest-EV such line; otherwise keep the pure-EV pick untouched (so a
    genuine blowout stays a clean sheet and clean-sheet margins are never
    inflated). realism=0 -> pure EV (unchanged). The band is relative so it
    behaves the same across stages despite very different scoring weights
    (group 3/1 vs final 30/20).
    """
    n = MAX_PICK_GOALS + 1
    if risk == "aggressive":
        sub = grid[:n, :n]
        i, j = np.unravel_index(int(sub.argmax()), sub.shape)
        return int(i), int(j)
    evs: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(n):
            ev = 0.0
            for a in range(grid.shape[0]):
                for b in range(grid.shape[1]):
                    ev += grid[a, b] * scoring.points((i, j), (a, b))
            evs[(i, j)] = ev
    best = max(evs, key=evs.get)
    best_ev = evs[best]
    if realism <= 0.0 or best_ev <= 0.0:
        return best
    threshold = best_ev * (1.0 - realism)
    btts = [s for s, ev in evs.items() if ev >= threshold and s[0] > 0 and s[1] > 0]
    if not btts:
        return best
    # highest-EV both-teams-score line within the band (tie-break: fewer goals)
    return max(btts, key=lambda s: (evs[s], -(s[0] + s[1])))


def top_scorelines(grid: np.ndarray, k: int = 5) -> list[ScorelineProb]:
    flat = [(grid[i, j], i, j) for i in range(grid.shape[0]) for j in range(grid.shape[1])]
    flat.sort(reverse=True)
    return [ScorelineProb(score=f"{i}-{j}", prob=round(float(p), 4)) for p, i, j in flat[:k]]


def predict_fixture(
    fixture: Fixture,
    scoring: Scoring | None = None,
    prefs: Preferences | None = None,
    risk: str = "safe",
) -> Prediction:
    scoring = scoring or Scoring()
    prefs = prefs or preferences()

    diff = ratings.effective_diff(fixture.home, fixture.away)
    we = poisson.elo_expectation(diff)
    lam_h, lam_a = poisson.calibrate_lambdas(we)
    grid = poisson.score_grid(lam_h, lam_a)
    p_home, p_draw, p_away = poisson.outcome_probs(grid)

    mi, mj = np.unravel_index(int(grid.argmax()), grid.shape)
    most_likely = f"{mi}-{mj}"

    override = prefs.override_for(fixture.home, fixture.away)
    if override and override.score:
        pi, pj = _parse_score(override.score)
        pick, reason, overridden = f"{pi}-{pj}", f"manual override ({override.note or 'preferences.yaml'})", True
    else:
        realism = prefs.strategy.realism
        pi, pj = best_pick(grid, scoring, risk, realism=realism)
        fav = fixture.home if p_home >= p_away else fixture.away
        reason = f"{risk} pick vs Elo edge to {fav} (Δ={diff:+.0f})"
        if realism > 0 and best_pick(grid, scoring, risk) != (pi, pj):
            reason += f" [realism {realism:g}]"
        pick = f"{pi}-{pj}"
        overridden = False

    return Prediction(
        fixture=fixture,
        pick=pick,
        pick_reason=reason,
        most_likely=most_likely,
        p_home=round(p_home, 4),
        p_draw=round(p_draw, 4),
        p_away=round(p_away, 4),
        expected_goals_home=round(lam_h, 2),
        expected_goals_away=round(lam_a, 2),
        top_scorelines=top_scorelines(grid),
        overridden=overridden,
    )
