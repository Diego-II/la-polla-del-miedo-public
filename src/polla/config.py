from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
PREFERENCES_PATH = ROOT / "config" / "preferences.yaml"


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore",
                                      populate_by_name=True)

    api_football_key: str | None = None
    api_football_url: str = "https://v3.football.api-sports.io"
    aws_region: str = "us-east-1"
    dynamo_table: str = "la-polla-del-miedo"

    @field_validator("api_football_url", mode="before")
    @classmethod
    def _ensure_scheme(cls, v):
        v = (v or "https://v3.football.api-sports.io").strip().rstrip("/")
        return v if v.startswith("http") else f"https://{v}"


def _key(name: str) -> str:
    return "".join(c for c in name.lower().strip() if c.isalnum())


class Override(BaseModel):
    match: str | None = None
    match_id: str | None = None
    score: str | None = None
    note: str | None = None


class Strategy(BaseModel):
    mode: str = "adaptive"  # safe | balanced | adaptive | aggressive
    comeback_aggression: float = 0.6
    # 0 = pure expected-points (all clean sheets). >0 trades a sliver of EV for
    # natural scorelines: among picks within `realism` of the best EV, prefer a
    # both-teams-score line. ~0.10 surfaces 2-1/1-1 in coin-flips, keeps blowouts.
    realism: float = 0.0


class Preferences(BaseModel):
    strategy: Strategy = Strategy()
    interesting_teams: list[str] = []
    overrides: list[Override] = []
    skip: list[str] = []

    @field_validator("interesting_teams", "overrides", "skip", mode="before")
    @classmethod
    def _none_to_list(cls, v):  # commented-out YAML keys parse as None
        return v or []

    @field_validator("strategy", mode="before")
    @classmethod
    def _none_to_strategy(cls, v):
        return v or {}

    def override_for(self, home: str, away: str, match_id: str | None = None) -> Override | None:
        want = (_key(home), _key(away))
        for ov in self.overrides:
            if match_id and ov.match_id and str(ov.match_id) == str(match_id):
                return ov
            if ov.match and " vs " in ov.match.lower():
                h, a = ov.match.lower().split(" vs ", 1)
                if (_key(h), _key(a)) == want:
                    return ov
        return None

    def is_interesting_team(self, team: str) -> bool:
        return _key(team) in {_key(t) for t in self.interesting_teams}


@lru_cache
def secrets() -> Secrets:
    return Secrets()


@lru_cache
def preferences() -> Preferences:
    if not PREFERENCES_PATH.exists():
        return Preferences()
    raw = yaml.safe_load(PREFERENCES_PATH.read_text()) or {}
    return Preferences.model_validate(raw)
