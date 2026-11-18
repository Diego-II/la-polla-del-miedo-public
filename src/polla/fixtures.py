from __future__ import annotations

import yaml

from .config import DATA
from .models import Fixture


def _doc() -> dict:
    return yaml.safe_load((DATA / "fixtures.yaml").read_text())


def first_matches() -> list[Fixture]:
    """The opening fixture of each of the 12 groups, ordered A..L."""
    groups = _doc()["groups"]
    out = []
    for g in sorted(groups):
        fm = groups[g]["first_match"]
        out.append(Fixture(group=g, home=fm["home"], away=fm["away"],
                           date=fm.get("date", ""), kickoff=fm.get("kickoff", "")))
    return out


def group_matches() -> list[Fixture]:
    """All group-stage fixtures (72), ordered by group then schedule."""
    groups = _doc()["groups"]
    out = []
    for g in sorted(groups):
        for m in groups[g].get("matches", []):
            out.append(Fixture(group=g, home=m["home"], away=m["away"],
                               date=m.get("date", ""), kickoff=m.get("kickoff", "")))
    return out
