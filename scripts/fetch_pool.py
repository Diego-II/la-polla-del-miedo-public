"""Build the WC2026 data pool FRUGALLY via the cache-first API-Football client.

Live-request budget plan (target: < 5 live calls total; cache hits are free):

  1. ``leagues?search=world cup``  -> discover the men's FIFA World Cup league
     (id 1) and confirm season 2026 is available.
  2. ``fixtures?league=1&season=2026`` -> ALL World Cup fixtures in ONE call.
  3. ``odds?fixture=<one id>``      -> proof-of-concept odds for ONE fixture.

Re-running this script is free: everything above is served from the on-disk
cache until its TTL expires.

Outputs:
  data/pool/fixtures.json   list[{fixture_id, date_utc, round, status,
                                   home, away, home_id, away_id}]
  data/pool/teams.json      list[{team_id, name}]
  data/pool/odds_sample.json the raw odds envelope for the sampled fixture(s)
"""
from __future__ import annotations

import json
from pathlib import Path

from polla.apifootball import ApiError, ApiFootball, BudgetExceeded
from polla.config import ROOT

POOL_DIR = ROOT / "data" / "pool"

WORLD_CUP_LEAGUE_ID = 1  # men's FIFA World Cup on API-Football
SEASON = 2026
# The FREE plan blocks fixtures for seasons outside ~2022-2024. We prefer 2026
# but fall back to the most recent ACCESSIBLE season so the pool pipeline still
# produces real data without burning the budget on doomed calls.
FREE_PLAN_SEASONS = [2022, 2023, 2024]
ODDS_SAMPLE_COUNT = 1     # keep the budget tiny: 1 fixture is enough as a PoC


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def discover_league(client: ApiFootball) -> dict:
    """Find the men's FIFA World Cup league and confirm season 2026."""
    results = client.get("leagues", search="world cup")
    chosen = None
    for item in results:
        league = item.get("league", {})
        name = (league.get("name") or "").lower()
        ltype = (league.get("type") or "").lower()
        seasons = {s.get("year") for s in item.get("seasons", [])}
        if (league.get("id") == WORLD_CUP_LEAGUE_ID
                and ltype == "cup" and "world cup" in name):
            chosen = item
            break
        # Fallback: first cup literally named "World Cup" offering season 2026.
        if "world cup" in name and ltype == "cup" and SEASON in seasons and chosen is None:
            chosen = item

    if chosen is None and results:
        chosen = results[0]  # last resort: whatever the search surfaced first
    if chosen is None:
        raise SystemExit("Could not discover the World Cup league from search results.")

    league = chosen["league"]
    seasons = sorted(s.get("year") for s in chosen.get("seasons", []))
    return {"id": league.get("id"), "name": league.get("name"),
            "type": league.get("type"), "seasons": seasons}


def normalize_fixtures(raw: list) -> tuple[list[dict], list[dict]]:
    """Flatten the API fixtures envelope into our pool shapes + derive teams."""
    fixtures: list[dict] = []
    teams: dict[int, str] = {}
    for item in raw:
        fx = item.get("fixture", {})
        league = item.get("league", {})
        home = item.get("teams", {}).get("home", {}) or {}
        away = item.get("teams", {}).get("away", {}) or {}
        fixtures.append({
            "fixture_id": fx.get("id"),
            "date_utc": fx.get("date"),
            "round": league.get("round"),
            "status": (fx.get("status") or {}).get("short"),
            "home": home.get("name"),
            "away": away.get("name"),
            "home_id": home.get("id"),
            "away_id": away.get("id"),
        })
        for t in (home, away):
            if t.get("id") is not None and t.get("name"):
                teams[t["id"]] = t["name"]

    fixtures.sort(key=lambda f: (f["date_utc"] or "", f["fixture_id"] or 0))
    team_list = [{"team_id": tid, "name": name}
                 for tid, name in sorted(teams.items(), key=lambda kv: kv[1])]
    return fixtures, team_list


def _is_plan_restriction(exc: ApiError) -> bool:
    return "do not have access" in str(exc).lower() or "plan" in str(exc).lower()


def fetch_fixtures_for_best_season(client: ApiFootball) -> tuple[int, list]:
    """Fetch ALL fixtures, preferring SEASON then falling back to free-plan ones.

    The free tier blocks fixtures for season 2026, so we degrade gracefully to
    the most recent accessible World Cup season instead of failing. Each attempt
    is at most ONE live call (and cached forever after).

    We try the most recent free-plan season first (errors don't cache, so a
    blocked season would otherwise re-spend budget on every run).
    """
    candidates = list(dict.fromkeys([*reversed(FREE_PLAN_SEASONS), SEASON]))
    for season in candidates:
        try:
            raw = client.get("fixtures", league=WORLD_CUP_LEAGUE_ID, season=season)
        except ApiError as exc:
            if _is_plan_restriction(exc):
                print(f"  [free-plan] season {season} blocked: {exc}")
                continue
            raise
        if raw:
            return season, raw
        print(f"  [empty] season {season} returned no fixtures; trying next.")
    return SEASON, []


def sample_odds(client: ApiFootball, fixtures: list[dict], season: int) -> list[dict]:
    """Fetch odds for the first ODDS_SAMPLE_COUNT fixtures (proof-of-concept)."""
    samples: list[dict] = []
    for fx in fixtures[:ODDS_SAMPLE_COUNT]:
        fid = fx.get("fixture_id")
        if fid is None:
            continue
        try:
            envelope = client.fetch("odds", fixture=fid)
        except BudgetExceeded as exc:
            print(f"  [skip] odds for fixture {fid}: {exc}")
            break
        except ApiError as exc:
            print(f"  [skip] odds for fixture {fid}: {exc}")
            break
        samples.append({"fixture_id": fid, "season": season,
                        "results": envelope.get("results"),
                        "response": envelope.get("response", [])})
    return samples


def main() -> None:
    client = ApiFootball()  # default cache dir + budget; real creds from secrets()
    start_remaining = client.remaining_today()

    # 1) Discover league + season ------------------------------------------
    league = discover_league(client)
    print(f"League found: id={league['id']} name={league['name']!r} "
          f"type={league['type']}")
    available = league["seasons"]
    season_ok = SEASON in available
    print(f"Season {SEASON} available: {season_ok} "
          f"(seasons: {available[:3]}{'...' if len(available) > 3 else ''})")

    # 2) ALL fixtures in ONE call (with free-plan season fallback) ---------
    used_season, raw_fixtures = fetch_fixtures_for_best_season(client)
    fixtures, teams = normalize_fixtures(raw_fixtures)
    _write_json(POOL_DIR / "fixtures.json", fixtures)
    _write_json(POOL_DIR / "teams.json", teams)
    if used_season != SEASON:
        print(f"NOTE: free plan blocks season {SEASON}; pool built from season "
              f"{used_season} (most recent accessible World Cup).")
    print(f"Season used for fixtures: {used_season}")
    print(f"Fixtures: {len(fixtures)}  ->  data/pool/fixtures.json")
    print(f"Teams:    {len(teams)}  ->  data/pool/teams.json")

    # 3) Odds proof-of-concept for 1 fixture (NOT all of them) -------------
    odds = sample_odds(client, fixtures, used_season)
    if odds:
        _write_json(POOL_DIR / "odds_sample.json", odds)
        print(f"Odds sample: {len(odds)} fixture(s)  ->  data/pool/odds_sample.json")
    else:
        print("Odds sample: none fetched.")

    # 4) Budget report ------------------------------------------------------
    used = max(0, start_remaining - client.remaining_today())
    print("-" * 60)
    print(f"LIVE API calls used this run: {used}")
    print(f"Total live calls today:       {client.used_today()}")
    print(f"Remaining daily budget:       {client.remaining_today()} / {client.max_per_day}")


if __name__ == "__main__":
    main()
