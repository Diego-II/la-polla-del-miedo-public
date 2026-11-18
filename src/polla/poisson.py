"""Dixon-Coles bivariate Poisson scoreline model, anchored to an Elo win
expectation. Goals are independent Poisson with a low-score correlation tweak."""
from __future__ import annotations

from math import factorial

import numpy as np

MAX_GOALS = 10
_FACT = np.array([float(factorial(k)) for k in range(MAX_GOALS + 1)])


def _poisson_pmf(lam: float) -> np.ndarray:
    k = np.arange(MAX_GOALS + 1)
    return np.exp(-lam) * lam**k / _FACT


def _dc_tau(lh: float, la: float, rho: float) -> np.ndarray:
    tau = np.ones((MAX_GOALS + 1, MAX_GOALS + 1))
    tau[0, 0] = 1 - lh * la * rho
    tau[0, 1] = 1 + lh * rho
    tau[1, 0] = 1 + la * rho
    tau[1, 1] = 1 - rho
    return tau


def score_grid(lam_home: float, lam_away: float, rho: float = 0.03) -> np.ndarray:
    """P(home=i, away=j) matrix, normalized."""
    grid = np.outer(_poisson_pmf(lam_home), _poisson_pmf(lam_away))
    grid *= _dc_tau(lam_home, lam_away, rho)
    return grid / grid.sum()


def outcome_probs(grid: np.ndarray) -> tuple[float, float, float]:
    p_home = float(np.tril(grid, -1).sum())  # home goals > away goals
    p_draw = float(np.trace(grid))
    p_away = float(np.triu(grid, 1).sum())
    return p_home, p_draw, p_away


def expected_home_score(grid: np.ndarray) -> float:
    """Elo-style expected match score for the home team: win + half a draw."""
    p_home, p_draw, _ = outcome_probs(grid)
    return p_home + 0.5 * p_draw


def elo_expectation(rating_diff: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-rating_diff / 400.0))


def calibrate_lambdas(
    win_expectation: float, total_goals: float = 2.6, rho: float = 0.03
) -> tuple[float, float]:
    """Find (lam_home, lam_away) summing to ~total_goals whose Poisson win
    expectation matches the Elo win_expectation. Bisection on supremacy."""
    lo, hi = -total_goals + 0.1, total_goals - 0.1
    for _ in range(60):
        s = (lo + hi) / 2
        lh = max(0.05, (total_goals + s) / 2)
        la = max(0.05, (total_goals - s) / 2)
        if expected_home_score(score_grid(lh, la, rho)) < win_expectation:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return max(0.05, (total_goals + s) / 2), max(0.05, (total_goals - s) / 2)
