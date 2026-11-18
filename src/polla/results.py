"""Actual-results tracker + form-based corrections for knockout predictions.

The Toqui predict-page payload carries, for every played game, the actual
score (``homeScore``/``awayScore``), a ``status`` ("finished") and the points
Toqui already graded for our pick. The group stage is LOCKED — picks can no
longer be edited — so what results are for now is:

  1. a running report of actual vs our picks (reports/results.{md,json}), and
  2. per-team "form vs model" corrections feeding the KNOCKOUT predictions:

         surprise/game = actual goal diff − model-expected goal diff
         elo adj       = K_FORM · mean(surprise) · played/(played+1), capped

     written to data/form_adjustments.yaml and applied on top of
     data/ratings.yaml by polla.ratings (set ``enabled: false`` there to opt
     out; the flag survives regeneration).

Expectations are computed against BASE ratings (``base=True``) so the file is
a pure function of (ratings.yaml, results) and never feeds back into itself.
eloratings refreshes already absorb results into the base Elo; the shrinkage
factor and cap keep this an intentionally modest nudge on top, not a second
full Elo update.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime

import yaml
from pydantic import BaseModel

from . import poisson, ratings, scoring
from .config import DATA, REPORTS
from .toqui import ToquiFixture

#: Elo points of correction per goal/game of surprise, and the hard cap.
K_FORM = 25.0
MAX_FORM_ADJ = 60

RESULTS_MD = REPORTS / "results.md"
RESULTS_JSON = REPORTS / "results.json"
ADJUSTMENTS_PATH = DATA / "form_adjustments.yaml"


class MatchResult(BaseModel):
    stage: str
    group: str | None = None
    home: str
    away: str
    home_score: int
    away_score: int
    pick: str | None = None   # our submitted pick "h-a", None if we had none
    points: int = 0           # Toqui-graded when available, else computed

    @property
    def actual(self) -> str:
        return f"{self.home_score}-{self.away_score}"

    @property
    def hit(self) -> str:
        """'exact' | 'outcome' | 'miss' | 'none' (no pick)."""
        if self.pick is None:
            return "none"
        ph, pa = (int(x) for x in self.pick.split("-"))
        if (ph, pa) == (self.home_score, self.away_score):
            return "exact"
        if (ph > pa) == (self.home_score > self.away_score) and \
                (ph == pa) == (self.home_score == self.away_score):
            return "outcome"
        return "miss"


class TeamForm(BaseModel):
    team: str                 # canonical (ratings.yaml) name
    played: int
    gf: int
    ga: int
    surprise_pg: float        # mean(actual GD − expected GD) per game
    adjustment: int           # suggested Elo correction (capped)


def _computed_points(fx: ToquiFixture) -> int:
    if fx.my_pick is None:
        return 0
    return scoring.for_toqui_stage(fx.stage).points(
        fx.my_pick, (fx.home_score, fx.away_score))


def finished_results(fixtures: Iterable[ToquiFixture]) -> list[MatchResult]:
    """Finished games with their actual score and our graded pick. Trusts
    Toqui's own ``points`` grading when present; computes from scoring.yaml
    otherwise (e.g. a freshly-finished game Toqui hasn't graded yet)."""
    out: list[MatchResult] = []
    for fx in fixtures:
        if fx.status != "finished" or fx.home_score is None or fx.away_score is None:
            continue
        out.append(MatchResult(
            stage=fx.stage,
            group=fx.group,
            home=fx.home,
            away=fx.away,
            home_score=fx.home_score,
            away_score=fx.away_score,
            pick=f"{fx.my_pick[0]}-{fx.my_pick[1]}" if fx.my_pick else None,
            points=fx.points if fx.points is not None else _computed_points(fx),
        ))
    return out


def expected_gd(home: str, away: str) -> float:
    """Model-expected goal difference (home − away) from BASE Elo."""
    diff = ratings.effective_diff(home, away, base=True)
    lam_h, lam_a = poisson.calibrate_lambdas(poisson.elo_expectation(diff))
    return lam_h - lam_a


def suggested_adjustment(surprise_pg: float, played: int) -> int:
    """Capped Elo correction with played/(played+1) shrinkage (1 game counts
    half, 3 games 75% — small samples shouldn't swing knockout picks hard)."""
    raw = K_FORM * surprise_pg * played / (played + 1)
    return int(round(max(-MAX_FORM_ADJ, min(MAX_FORM_ADJ, raw))))


def team_form(results: Iterable[MatchResult],
              expected_gd_fn: Callable[[str, str], float] = expected_gd,
              ) -> list[TeamForm]:
    """Per-team group-stage form vs the model, sorted by |surprise| desc."""
    acc: dict[str, dict] = {}
    for r in results:
        if r.stage != "group":
            continue
        exp = expected_gd_fn(r.home, r.away)
        gd = r.home_score - r.away_score
        for team, gf, ga, surprise in (
            (r.home, r.home_score, r.away_score, gd - exp),
            (r.away, r.away_score, r.home_score, -gd + exp),
        ):
            name = ratings.canonical(team)
            t = acc.setdefault(name, {"played": 0, "gf": 0, "ga": 0, "surprises": []})
            t["played"] += 1
            t["gf"] += gf
            t["ga"] += ga
            t["surprises"].append(surprise)
    forms = []
    for name, t in acc.items():
        spg = sum(t["surprises"]) / len(t["surprises"])
        forms.append(TeamForm(
            team=name, played=t["played"], gf=t["gf"], ga=t["ga"],
            surprise_pg=round(spg, 2),
            adjustment=suggested_adjustment(spg, t["played"]),
        ))
    forms.sort(key=lambda f: (-abs(f.surprise_pg), f.team))
    return forms


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
_HIT_LABEL = {"exact": "✅ exact", "outcome": "🟡 outcome", "miss": "❌ miss",
              "none": "— no pick"}


def render_results_md(results: list[MatchResult], forms: list[TeamForm],
                      generated: datetime) -> str:
    total = sum(r.points for r in results)
    counts = {k: sum(1 for r in results if r.hit == k)
              for k in ("exact", "outcome", "miss", "none")}
    lines = [
        "# Results — actual vs our picks",
        "",
        f"_Updated: {generated.isoformat(timespec='seconds')}_",
        "",
        f"**Points so far: {total}** · {len(results)} finished · "
        f"{counts['exact']} exact · {counts['outcome']} outcome-only · "
        f"{counts['miss']} missed",
        "",
        "| Stage | Grp | Match | Actual | Our pick | Hit | Pts |",
        "|-------|-----|-------|--------|----------|-----|-----|",
    ]
    for r in results:
        lines.append(f"| {r.stage} | {r.group or '—'} | {r.home} vs {r.away} "
                     f"| **{r.actual}** | {r.pick or '—'} "
                     f"| {_HIT_LABEL[r.hit]} | {r.points} |")
    lines += ["", "## Team form vs model (group stage)", ""]
    if forms:
        lines += [
            "| Team | GP | GF–GA | Surprise GD/g | Elo correction |",
            "|------|----|-------|---------------|----------------|",
        ]
        lines += [f"| {f.team} | {f.played} | {f.gf}–{f.ga} "
                  f"| {f.surprise_pg:+.2f} | {f.adjustment:+d} |"
                  for f in forms]
    else:
        lines.append("_No finished group matches yet._")
    lines += [
        "",
        "_Surprise = actual − model-expected goal difference (base Elo)._",
        "_Corrections are written to `data/form_adjustments.yaml` and applied",
        "to knockout model predictions; set `enabled: false` there to opt out._",
        "_Group picks are locked — they are never edited or resubmitted._",
    ]
    return "\n".join(lines) + "\n"


def render_adjustments_yaml(forms: list[TeamForm], generated: datetime,
                            enabled: bool = True) -> str:
    lines = [
        "# AUTO-GENERATED by the daily-update job (polla.results) — values are",
        "# overwritten on every run; only the `enabled` flag is preserved.",
        "# Per-team Elo corrections from group-stage results: actual vs",
        "# model-expected goal difference, K_FORM=%g per goal/game with" % K_FORM,
        "# played/(played+1) shrinkage, capped at ±%d. Applied on top of" % MAX_FORM_ADJ,
        "# data/ratings.yaml for model predictions (group picks are locked, so",
        "# effectively this shapes the knockout picks only).",
        f"generated: '{generated.isoformat(timespec='seconds')}'",
        f"enabled: {'true' if enabled else 'false'}",
        "adjustments:",
    ]
    relevant = [f for f in forms if f.adjustment != 0]
    if not relevant:
        lines[-1] = "adjustments: {}"
        return "\n".join(lines) + "\n"
    lines += [f"  {f.team}: {f.adjustment}" for f in relevant]
    lines.append("notes:")
    lines += [f"  {f.team}: {f.played} GP, {f.gf}-{f.ga}, "
              f"surprise {f.surprise_pg:+.2f} GD/g" for f in relevant]
    return "\n".join(lines) + "\n"


def adjustments_enabled(path=ADJUSTMENTS_PATH) -> bool:
    """The user-controlled opt-out flag of the existing file (default True)."""
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except OSError:
        return True
    return bool(doc.get("enabled", True))


def write_all(fixtures: Iterable[ToquiFixture], now: datetime) -> dict:
    """Write reports/results.{md,json} + data/form_adjustments.yaml. Returns a
    small summary for logging. Clears the ratings adjustment cache so the new
    corrections take effect in the same process."""
    results = finished_results(fixtures)
    forms = team_form(results)

    REPORTS.mkdir(exist_ok=True)
    RESULTS_MD.write_text(render_results_md(results, forms, now))
    RESULTS_JSON.write_text(json.dumps({
        "updated": now.isoformat(timespec="seconds"),
        "total_points": sum(r.points for r in results),
        "results": [r.model_dump() | {"actual": r.actual, "hit": r.hit}
                    for r in results],
        "team_form": [f.model_dump() for f in forms],
    }, indent=2, ensure_ascii=False) + "\n")

    ADJUSTMENTS_PATH.write_text(
        render_adjustments_yaml(forms, now, enabled=adjustments_enabled()))
    ratings._adjustments.cache_clear()

    return {"finished": len(results),
            "points": sum(r.points for r in results),
            "corrections": sum(1 for f in forms if f.adjustment != 0)}
