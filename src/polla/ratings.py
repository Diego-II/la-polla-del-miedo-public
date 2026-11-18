from __future__ import annotations

import unicodedata
from functools import lru_cache

import yaml

from .config import DATA

DEFAULT_ELO = 1600.0

# Map external provider names (Toqui/sportmonks) to our ratings.yaml names.
# Keyed by normalized form; value is the canonical name in data/ratings.yaml.
_ALIASES = {
    "korearepublic": "South Korea",
    "capeverdeislands": "Cape Verde",
    "congodr": "DR Congo",
    "cotedivoire": "Ivory Coast",
    "curacao": "Curaçao",
    "turkiye": "Turkey",
    "usa": "United States",
}


def _key(name: str) -> str:
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return "".join(c for c in n.lower() if c.isalnum())


#: Optional per-team Elo corrections derived from actual tournament results
#: (written by the daily-update job — see polla.results). Applied on top of
#: ratings.yaml unless the file sets ``enabled: false``.
FORM_ADJUSTMENTS = DATA / "form_adjustments.yaml"


@lru_cache
def _data() -> dict:
    return yaml.safe_load((DATA / "ratings.yaml").read_text())


@lru_cache
def _ratings() -> dict[str, float]:
    return {_key(k): float(v) for k, v in _data()["ratings"].items()}


@lru_cache
def _adjustments() -> dict[str, float]:
    try:
        doc = yaml.safe_load(FORM_ADJUSTMENTS.read_text()) or {}
    except OSError:
        return {}
    if not doc.get("enabled", True):
        return {}
    return {_key(k): float(v) for k, v in (doc.get("adjustments") or {}).items()}


def canonical(team: str) -> str:
    """The ratings.yaml canonical name for a provider name (e.g. 'Korea
    Republic' -> 'South Korea'); unknown names pass through unchanged."""
    return _ALIASES.get(_key(team), team)


def rating(team: str, *, base: bool = False) -> float:
    """Team Elo. ``base=True`` ignores form adjustments — used when DERIVING
    them, so regenerating the adjustment file never feeds back into itself."""
    r = _ratings()
    k = _key(team)
    if k not in r:
        alias = _ALIASES.get(k)
        if not (alias and _key(alias) in r):
            return DEFAULT_ELO
        k = _key(alias)
    value = r[k]
    if not base:
        value += _adjustments().get(k, 0.0)
    return value


def _hosts() -> set[str]:
    return {_key(t) for t in _data().get("host_nations", [])}


def effective_diff(home: str, away: str, *, base: bool = False) -> float:
    """Elo difference from the home team's perspective, including host advantage."""
    diff = rating(home, base=base) - rating(away, base=base)
    if _key(home) in _hosts():
        diff += float(_data().get("home_advantage_elo", 0))
    if _key(away) in _hosts():
        diff -= float(_data().get("home_advantage_elo", 0))
    return diff
