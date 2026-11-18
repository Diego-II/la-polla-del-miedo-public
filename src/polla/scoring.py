from __future__ import annotations

from functools import lru_cache

import yaml

from .config import DATA
from .models import Scoring


@lru_cache
def _data() -> dict:
    return yaml.safe_load((DATA / "scoring.yaml").read_text())


#: Toqui's stage strings (as emitted in the predict-page flight) -> our
#: scoring.yaml stage keys.
TOQUI_STAGES = {
    "group": "GRP",
    "round-of-32": "R32",
    "round-of-16": "R16",
    "quarter-final": "QF",
    "semi-final": "SF",
    "third-place": "THIRD",
    "final": "FIN",
}


def for_stage(stage: str = "GRP") -> Scoring:
    s = _data()["stages"][stage]
    return Scoring(outcome=s["outcome"], exact=s["exact"])


def for_toqui_stage(stage: str) -> Scoring:
    """Scoring for a Toqui stage string (e.g. ``round-of-32`` -> R32 weights)."""
    return for_stage(TOQUI_STAGES.get(stage, "GRP"))


def lock_time(stage: str = "GRP") -> str:
    return _data()["stages"][stage]["lock"]
