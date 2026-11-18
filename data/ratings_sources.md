# National-team ratings: source evaluation

Goal: pick the best **free**, reasonably-current national-team strength ratings
for the 48 World Cup 2026 squads, to drive `polla.ratings` / `polla.poisson`.
The model only needs a sound *relative ordering* on an Elo-like scale (it turns
the Elo **difference** into a win expectation), so any consistent national-team
Elo index fits the schema directly.

## Chosen source: World Football Elo (eloratings.net) ✅

**Why:** It is the canonical free national-team Elo index, it is current, and it
publishes two clean, machine-readable data files that map 1:1 onto our schema:

| File | Shape | Use |
| --- | --- | --- |
| `https://www.eloratings.net/en.teams.tsv` | `CODE\tCanonical\tAlias…` | name → country-code map |
| `https://www.eloratings.net/2026.tsv` | row per team, col 3 = current Elo | code → Elo |

- **Coverage:** 47/48 squads resolve by canonical name out of the box. The only
  mismatch is **Czech Republic**, which eloratings lists as *Czechia* (code `CZ`)
  — handled with a single alias in `scripts/refresh_ratings.py` (`NAME_ALIASES`).
- **Scale:** values land in ~1423 (Qatar) … 2165 (Spain), squarely inside the
  test's sane band `[1300, 2200]` and on the same Elo axis the model already
  used, so `home_advantage_elo: 70` keeps the same meaning.
- **Cost:** two static TSV downloads, cached under `data_cache/eloratings/`.
  No API key, no rate limit, no season gating. Re-runs are cache-first/idempotent.

It directly replaces the hand-curated snapshot and improves accuracy by using
live, consistently-computed match-result Elo instead of eyeballed numbers.

### Curated → eloratings deltas (notable)

The live Elo reorders several teams vs. the old hand-curated guesses, e.g.:

| Team | Old (curated) | New (eloratings 2026) |
| --- | ---: | ---: |
| Spain | 2120 | 2165 |
| France | 2080 | 2081 |
| Argentina | 2100 | 2113 |
| Brazil | 2040 | 1988 |
| Germany | 1965 | 1925 |
| Turkey | 1820 | 1906 |
| Ecuador | 1850 | 1935 |
| Norway | 1850 | 1917 |
| Senegal | 1830 | 1867 |
| USA | 1800 | 1733 |
| Mexico | 1800 | 1867 |
| Qatar | 1690 | 1423 |
| New Zealand | 1500 | 1563 |
| Haiti | 1540 | 1554 |

(USA, Brazil, Germany and Qatar in particular were over-rated in the curated
snapshot relative to the live Elo.)

## Sources evaluated and rejected

### soccerdata readers
`import setuptools` is required before `import soccerdata` (Python 3.13 distutils
shim). Available readers: `ClubElo, ESPN, FBref, FiveThirtyEight, FotMob,
MatchHistory, SoFIFA, Sofascore, Understat, WhoScored`.

- **ClubElo** — clubs only, no national teams. Not useful here.
- **FBref / FotMob / Sofascore / SoFIFA / ESPN / Understat / WhoScored** — these
  expose **club/league** match data, fixtures, and (for SoFIFA) FIFA *video-game*
  club/player attributes. None publishes a clean, current **national-team
  strength index** keyed by country that beats eloratings for this use case, and
  scraping per-team pages would be slower and more fragile than two static TSVs.
  Not worth the time given a perfect direct match exists.

### API-Football (free tier)
Already wired via `polla.apifootball.ApiFootball` (cache-first, budget-guarded).
The current World Cup **season (2026) is blocked on the free tier**, and the free
tier has no non-season-gated FIFA-ranking-like endpoint that yields a numeric
strength scale comparable to Elo. `/teams` returns metadata (name/country/logo),
not ratings. **Conclusion: no genuinely useful, accessible strength signal on the
free tier for this task — so zero live API-Football calls were spent** (budget
preserved).

## Refresh / reproduce

```bash
uv run python scripts/refresh_ratings.py            # cache-first, idempotent
uv run python scripts/refresh_ratings.py --refresh  # force re-download
uv run python scripts/refresh_ratings.py --dry-run  # print, don't write
```

Raw fetches + provenance are cached under `data_cache/eloratings/`
(`en.teams.tsv`, `2026.tsv`, and `*.meta.json` recording URL + fetch time).
If any squad fails to resolve, the script **refuses to overwrite** the file and
exits non-zero, so a bad fetch can never silently flatten the ratings.
