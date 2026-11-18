from __future__ import annotations

import numpy as np
from pydantic import BaseModel


class Scoring(BaseModel):
    """Toqui scoring. Exact is ADDITIVE on top of outcome: nailing the score
    earns outcome + exact. Defaults to the group stage; later stages pay more
    (see data/scoring.yaml)."""

    outcome: int = 1
    exact: int = 3

    def points(self, pred: tuple[int, int], actual: tuple[int, int]) -> int:
        pts = 0
        if np.sign(pred[0] - pred[1]) == np.sign(actual[0] - actual[1]):
            pts += self.outcome
        if pred == actual:
            pts += self.exact
        return pts


class Fixture(BaseModel):
    group: str
    home: str
    away: str
    date: str = ""
    kickoff: str = ""

    @property
    def label(self) -> str:
        return f"{self.home} vs {self.away}"


class ScorelineProb(BaseModel):
    score: str  # "home-away"
    prob: float


class Prediction(BaseModel):
    fixture: Fixture
    pick: str  # recommended scoreline "home-away"
    pick_reason: str
    most_likely: str
    p_home: float
    p_draw: float
    p_away: float
    expected_goals_home: float
    expected_goals_away: float
    top_scorelines: list[ScorelineProb]
    overridden: bool = False
