"""Pool-site adapter for Toqui (the third-party site that hosted the pool).

PUBLIC MIRROR NOTE: the site-specific integration was removed from this copy.
The private repo had a browser-driven client here that logged in, read the
fixtures and standings pages, and submitted picks. The login flow, the
endpoints, the pool identifiers and the submit request are not published.

What is left:
  * the data shapes the rest of the code depends on (``ToquiFixture``,
    ``ToquiStanding``),
  * the pure parsing helpers that turn a saved page payload into those shapes
    (unit-tested against synthetic samples in ``tests/_samples``),
  * ``ToquiClient``, a STUB with the original interface. Every network method
    raises ``NotImplementedError``. The jobs in ``jobs/`` are written against
    this interface and are tested with a fake client.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import TracebackType
from typing import Any

# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #
@dataclass
class ToquiFixture:
    match_id: str
    stage: str
    home: str
    away: str
    kickoff_utc: str | None
    lock_utc: str | None
    my_pick: tuple[int, int] | None
    # Result fields (populated by Toqui once a game kicks off / finishes).
    status: str | None = None            # "scheduled" | "finished" | live states
    home_score: int | None = None        # actual goals (None until played)
    away_score: int | None = None
    points: int | None = None            # points Toqui graded for our pick
    group: str | None = None             # "A".."L" for group games, None later
    is_placeholder: bool = False         # knockout slot not yet resolved


@dataclass
class ToquiStanding:
    rank: int
    name: str
    points: int


# --------------------------------------------------------------------------- #
# Pure parsing helpers (unit-tested; no network)
# --------------------------------------------------------------------------- #
_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[\d+,("(?:[^"\\]|\\.)*")\]\)', re.S)


def decode_flight(html: str) -> str:
    """Concatenate + JSON-decode every ``self.__next_f.push([n,"..."])`` string
    literal in a Next.js page, recovering the underlying RSC flight text."""
    parts: list[str] = []
    for lit in _PUSH_RE.findall(html):
        try:
            parts.append(json.loads(lit))
        except (json.JSONDecodeError, ValueError):
            continue
    return "".join(parts)


def _extract_array(text: str, start_pattern: str) -> Any:
    """Return the JSON array whose opening ``[`` matches ``start_pattern``.

    Performs a string-aware balanced-bracket scan so nested arrays/strings do
    not terminate the scan early.
    """
    m = re.search(start_pattern, text)
    if not m:
        return None
    start = m.start()
    if text[start] != "[":
        start = text.index("[", start)
    depth = 0
    i = start
    instr = False
    esc = False
    while i < len(text):
        c = text[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        i += 1
    return None


def parse_games_flight(html: str) -> list[dict]:
    """Extract the raw ``games`` array of game dicts from a /predict page HTML."""
    flight = decode_flight(html)
    arr = _extract_array(flight, r'"games":\[')
    return arr or []


def parse_standings_flight(html: str) -> list[dict]:
    """Extract the raw standings array from a /standings page HTML.

    Matches the array whose first object carries both a ``rank`` and a nested
    ``user`` (the leaderboard entries), tolerating either ``"standings":[`` or
    a bare ``[{...,"rank":...,"user":...}]`` shape.
    """
    flight = decode_flight(html)
    for pat in (r'"standings":\[',
                r'\[\{[^\[\]]*"rank":[^\[\]]*"user":',
                r'\[\{[^\[\]]*"userId":'):
        arr = _extract_array(flight, pat)
        if arr:
            return arr
    return []


def _pick_from_prediction(pred: Any) -> tuple[int, int] | None:
    """Extract ``(home, away)`` from a Toqui prediction object, tolerating BOTH
    shapes the app uses:

    * the **predict-page flight** nests it as ``{"homeScore", "awayScore"}``
      (this is what the live `/predict` page actually emits), and
    * the **REST predictions** / older flight use
      ``{"predictedHomeScore", "predictedAwayScore"}``.

    Returns ``None`` if there is no prediction or either score is missing.

    NOTE: reading the wrong key here was the original "0 picks persisted" bug —
    writes were landing, but the reader looked only for ``predictedHomeScore``
    and so reported every saved pick as ``None``.
    """
    if not isinstance(pred, dict):
        return None
    h = pred.get("homeScore")
    a = pred.get("awayScore")
    if h is None:
        h = pred.get("predictedHomeScore")
    if a is None:
        a = pred.get("predictedAwayScore")
    if h is None or a is None:
        return None
    return int(h), int(a)


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def game_to_fixture(g: dict) -> ToquiFixture:
    """Map one raw Toqui game dict to a :class:`ToquiFixture`."""
    my_pick = _pick_from_prediction(g.get("prediction"))
    return ToquiFixture(
        match_id=str(g.get("id")),
        stage=str(g.get("stage")),
        home=str(g.get("homeTeam")),
        away=str(g.get("awayTeam")),
        kickoff_utc=g.get("kickoffAt"),
        lock_utc=g.get("locksAt"),
        my_pick=my_pick,
        status=g.get("status"),
        home_score=_int_or_none(g.get("homeScore")),
        away_score=_int_or_none(g.get("awayScore")),
        points=_int_or_none(g.get("points")),
        group=g.get("group"),
        is_placeholder=bool(g.get("isPlaceholder")),
    )


def standing_to_obj(s: dict) -> ToquiStanding:
    """Map one raw Toqui standings entry to a :class:`ToquiStanding`."""
    user = s.get("user") or {}
    name = user.get("displayName") or user.get("name") or s.get("name") or "?"
    return ToquiStanding(
        rank=int(s.get("rank", 0)),
        name=str(name),
        points=int(s.get("points", 0)),
    )


def parse_fixtures(html: str) -> list[ToquiFixture]:
    """Full /predict HTML -> list[ToquiFixture]."""
    return [game_to_fixture(g) for g in parse_games_flight(html)]


def parse_standings(html: str) -> list[ToquiStanding]:
    """Full /standings HTML -> list[ToquiStanding]."""
    return [standing_to_obj(s) for s in parse_standings_flight(html)]


_PLACEHOLDER_RE = re.compile(
    r"\b(winner|loser|runner|\d(?:st|nd|rd|th)\s+position|"
    r"position\b|group\s+[a-l]\b|semi-final|quarter-final|round of)\b",
    re.IGNORECASE)


def _is_placeholder_team(name: str | None) -> bool:
    """True for not-yet-determined knockout slots like '2nd position Group A'
    or 'Winner Semi-final 1' (these cards have no score inputs yet)."""
    if not name:
        return True
    return bool(_PLACEHOLDER_RE.search(name))


def is_locked(fixture: ToquiFixture, *, now: datetime | None = None) -> bool:
    """True if the fixture's lock time has passed (predictions no longer editable)."""
    if not fixture.lock_utc:
        return False
    now = now or datetime.now(timezone.utc)
    lock = datetime.fromisoformat(fixture.lock_utc.replace("Z", "+00:00"))
    return now >= lock


# --------------------------------------------------------------------------- #
# Client STUB (site integration removed from the public mirror)
# --------------------------------------------------------------------------- #
_REMOVED = ("The pool-site integration was removed from the public mirror. "
            "Implement this method against your own pool provider.")


class ToquiClient:
    """STUB. Same interface as the private client, no implementation.

    Contract the jobs rely on::

        with ToquiClient(headless=True) as t:
            t.login()
            fixtures = t.fixtures()                  # list[ToquiFixture]
            standings = t.standings()                # list[ToquiStanding]
            t.submit_pick(match_id, 2, 1)            # dry-run by DEFAULT
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def __enter__(self) -> "ToquiClient":
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, tb: TracebackType | None) -> None:
        return None

    def login(self) -> None:
        raise NotImplementedError(_REMOVED)

    def fixtures(self) -> list[ToquiFixture]:
        raise NotImplementedError(_REMOVED)

    def standings(self) -> list[ToquiStanding]:
        raise NotImplementedError(_REMOVED)

    def predictions(self) -> dict[str, tuple[int, int]]:
        raise NotImplementedError(_REMOVED)

    def submit_pick(self, match_id: str, home_goals: int, away_goals: int,
                    *, dry_run: bool = True, advancer_id: str | None = None,
                    verify: bool = False,
                    fixtures: list[ToquiFixture] | None = None) -> bool:
        raise NotImplementedError(_REMOVED)


__all__ = [
    "ToquiClient",
    "ToquiFixture",
    "ToquiStanding",
    "parse_fixtures",
    "parse_standings",
    "parse_games_flight",
    "parse_standings_flight",
    "game_to_fixture",
    "standing_to_obj",
    "decode_flight",
    "is_locked",
    "_pick_from_prediction",
]
