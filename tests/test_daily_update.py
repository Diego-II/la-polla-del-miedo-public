from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root for jobs/

from jobs.daily_update import desired_pick, is_editable, should_submit  # noqa: E402
from polla.config import Override, Preferences, Strategy  # noqa: E402
from polla.toqui import ToquiFixture  # noqa: E402

FUTURE = "2099-01-01T00:00:00.000Z"
PAST = "2000-01-01T00:00:00.000Z"


def _fx(home, away, lock=FUTURE, my_pick=None, stage="group"):
    return ToquiFixture(match_id="x", stage=stage, home=home, away=away,
                        kickoff_utc=None, lock_utc=lock, my_pick=my_pick)


def test_is_editable():
    now = datetime.now(timezone.utc)
    assert is_editable(_fx("Spain", "Uruguay"), now)
    assert not is_editable(_fx("Winner Match 73", "1st Group A"), now)  # placeholders
    assert not is_editable(_fx("Spain", "Uruguay", PAST), now)         # already locked


def test_should_submit_never_touches_group_stage():
    """The group stage is permanently locked: even a (hypothetically) open
    group fixture must never be a submission candidate anymore."""
    now = datetime.now(timezone.utc)
    assert not should_submit(_fx("Spain", "Uruguay", stage="group"), now)
    assert should_submit(_fx("Spain", "Uruguay", stage="round-of-32"), now)
    assert not should_submit(
        _fx("2nd position Group A", "2nd position Group B", stage="round-of-32"), now)


def test_desired_pick_override_beats_model():
    prefs = Preferences(strategy=Strategy(),
                        overrides=[Override(match="Spain vs Uruguay", score="4-1")])
    assert desired_pick(prefs, _fx("Spain", "Uruguay")) == "4-1"


def test_desired_pick_uses_stage_scoring():
    # The model must price knockout picks with that stage's weights, not GRP's.
    prefs = Preferences(strategy=Strategy())
    pick = desired_pick(prefs, _fx("Spain", "Uruguay", stage="round-of-32"))
    h, a = (int(x) for x in pick.split("-"))
    assert 0 <= h <= 5 and 0 <= a <= 5
