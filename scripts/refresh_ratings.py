#!/usr/bin/env python
"""Refresh ``data/ratings.yaml`` from World Football Elo (eloratings.net).

Why eloratings.net?
-------------------
The prediction model only needs a *relative*, reasonably-current national-team
strength scale on an Elo-like axis (``polla.ratings`` / ``polla.poisson`` turn
the Elo *difference* into a win expectation). eloratings.net is the canonical
free national-team Elo source and it publishes two clean, machine-readable
data files that map 1:1 onto our schema:

  * ``en.teams.tsv``  -> ``<country-code>\t<canonical name>\t<aliases...>``
  * ``<year>.tsv``    -> one row per team, ``... <code> <elo> ...`` (col 3 = Elo)

soccerdata's ``ClubElo`` is CLUBS-only; its other readers expose club/league
match data, not a national-team strength index. API-Football's free tier has no
non-season-gated FIFA-ranking-like signal we can use for 2026. So eloratings.net
is both the best fit and a direct schema match. See ``data/ratings_sources.md``.

Behaviour
---------
* CACHE-FIRST + idempotent. Raw TSV fetches are written under
  ``data_cache/eloratings/`` and reused while fresh, so re-running costs no
  network. ``--refresh`` forces a re-download.
* Schema-preserving. ``home_advantage_elo`` and ``host_nations`` are carried
  over verbatim from the existing file; only ``ratings``, ``as_of`` and
  ``source`` are rewritten.
* Safe. If the fetch cannot cover all 48 squads, the script refuses to clobber
  the curated file and exits non-zero.

Usage::

    uv run python scripts/refresh_ratings.py            # cache-first
    uv run python scripts/refresh_ratings.py --refresh  # force re-download
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE_DIR = ROOT / "data_cache" / "eloratings"
RATINGS_PATH = DATA / "ratings.yaml"

ELO_YEAR = 2026
TEAMS_URL = "https://www.eloratings.net/en.teams.tsv"
RATINGS_URL = f"https://www.eloratings.net/{ELO_YEAR}.tsv"
SOURCE_LABEL = f"World Football Elo (eloratings.net {ELO_YEAR}.tsv)"

USER_AGENT = "Mozilla/5.0 (compatible; la-polla-del-miedo/1.0)"
CACHE_TTL_DAYS = 7

#: Squad names whose spelling differs from eloratings.net's canonical name.
#: Left = our schema key, right = the lowercased name eloratings uses.
NAME_ALIASES = {
    "Czech Republic": "czechia",
}


# ---------------------------------------------------------------------------
# fetch + cache (raw TSV under data_cache/eloratings/)
# ---------------------------------------------------------------------------

def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted host)
        return resp.read().decode("utf-8", "replace")


def _cache_path(name: str) -> Path:
    return CACHE_DIR / name


def _is_fresh(path: Path, *, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    )
    return age.days < ttl_days


def fetch_tsv(url: str, cache_name: str, *, refresh: bool = False) -> str:
    """Return TSV text for ``url``, cache-first under ``data_cache/eloratings``."""
    path = _cache_path(cache_name)
    if not refresh and _is_fresh(path):
        return path.read_text(encoding="utf-8")
    text = _http_get(url)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    # Record provenance alongside the raw file.
    (_cache_path(cache_name + ".meta.json")).write_text(
        json.dumps(
            {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
             "bytes": len(text)},
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def parse_name_map(teams_tsv: str) -> dict[str, str]:
    """Map every eloratings team name/alias (lowercased) -> country code.

    Lines look like ``CODE\tCanonical\tAlias1\tAlias2`` (location helper rows
    use a ``_loc`` code suffix and are skipped).
    """
    name2code: dict[str, str] = {}
    for line in teams_tsv.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0]
        if code.endswith("_loc"):
            continue
        for name in parts[1:]:
            name = name.strip()
            if name:
                name2code.setdefault(name.lower(), code)
    return name2code


def parse_ratings(ratings_tsv: str) -> dict[str, float]:
    """Map country code -> current Elo (column index 3 in the year TSV)."""
    code2elo: dict[str, float] = {}
    for line in ratings_tsv.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        code = parts[2]
        try:
            code2elo[code] = float(parts[3])
        except ValueError:
            continue
    return code2elo


def resolve_squads(
    squads: list[str], name2code: dict[str, str], code2elo: dict[str, float],
) -> tuple[dict[str, float], list[str]]:
    """Return ``{squad: elo}`` plus a list of squads that could not be resolved."""
    out: dict[str, float] = {}
    missing: list[str] = []
    for squad in squads:
        lookup = NAME_ALIASES.get(squad, squad).lower()
        code = name2code.get(lookup)
        elo = code2elo.get(code) if code else None
        if elo is None:
            missing.append(squad)
        else:
            out[squad] = round(elo)
    return out, missing


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def build_document(existing: dict, ratings: dict[str, float]) -> dict:
    """Assemble the new YAML doc, preserving schema + carried-over fields."""
    return {
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": SOURCE_LABEL,
        # Carry these over verbatim so the model keeps behaving identically.
        "home_advantage_elo": existing.get("home_advantage_elo", 70),
        "host_nations": existing.get("host_nations",
                                     ["Mexico", "United States", "Canada"]),
        # Preserve the original squad order from the curated file.
        "ratings": {k: ratings[k] for k in existing["ratings"] if k in ratings},
    }


def dump_yaml(doc: dict) -> str:
    header = (
        f"# National-team strength ratings (World Football Elo, eloratings.net).\n"
        f"# Auto-generated by scripts/refresh_ratings.py on {doc['as_of']}.\n"
        f"# Relative ordering/scale drives predictions; re-run to refresh.\n"
    )
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000)
    return header + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="force re-download, ignoring fresh cache")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve + print, but do not write ratings.yaml")
    args = parser.parse_args(argv)

    existing = yaml.safe_load(RATINGS_PATH.read_text())
    squads = list(existing["ratings"].keys())

    teams_tsv = fetch_tsv(TEAMS_URL, "en.teams.tsv", refresh=args.refresh)
    ratings_tsv = fetch_tsv(RATINGS_URL, f"{ELO_YEAR}.tsv", refresh=args.refresh)

    name2code = parse_name_map(teams_tsv)
    code2elo = parse_ratings(ratings_tsv)
    resolved, missing = resolve_squads(squads, name2code, code2elo)

    if missing:
        print(f"ERROR: could not resolve {len(missing)} squad(s): {missing}",
              file=sys.stderr)
        print("Refusing to overwrite the curated file.", file=sys.stderr)
        return 1

    doc = build_document(existing, resolved)
    yaml_text = dump_yaml(doc)

    if args.dry_run:
        print(yaml_text)
        return 0

    RATINGS_PATH.write_text(yaml_text, encoding="utf-8")
    print(f"Wrote {RATINGS_PATH.relative_to(ROOT)} "
          f"with {len(doc['ratings'])} teams from {SOURCE_LABEL}.")
    lo = min(doc["ratings"].items(), key=lambda kv: kv[1])
    hi = max(doc["ratings"].items(), key=lambda kv: kv[1])
    print(f"  range: {lo[0]} {lo[1]}  ..  {hi[0]} {hi[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
