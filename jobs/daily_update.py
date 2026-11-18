#!/usr/bin/env python3
"""The one daily job. The GROUP STAGE IS LOCKED (no group pick can change), so
the daily duties are now, in order:
  1. refresh team strength (eloratings — absorbs friendly + competitive results),
  2. fetch ACTUAL RESULTS from Toqui, compare with our picks, and rewrite
     reports/results.{md,json} (running points tracker),
  3. derive per-team form corrections from the group results and rewrite
     data/form_adjustments.yaml — these feed the KNOCKOUT model predictions
     (set `enabled: false` in that file to opt out),
  4. recompute predictions and rewrite reports/group_stage.{md,json} (audit
     trail only — group picks can no longer be submitted),
  5. re-submit ONLY changed picks for editable KNOCKOUT matches (real teams,
     not locked); group matches are never touched,
  6. snapshot predictions to DynamoDB + append a changelog entry (--live).

Toqui's current pick is the source of truth for "what's submitted". Reports
are always rewritten; DynamoDB/Toqui writes + changelog happen only with --live.

    uv run python jobs/daily_update.py            # dry-run (default)
    uv run python jobs/daily_update.py --live     # apply changes
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

from polla import changelog, ratings, report, results, scoring
from polla.config import REPORTS, ROOT, preferences
from polla.fixtures import group_matches
from polla.models import Fixture
from polla.predict import predict_fixture
from polla.store import log_submission, put_prediction
from polla.toqui import ToquiClient, ToquiFixture

TITLE = "La Polla del Miedo — Group stage (all 72 matches)"


def refresh_elo() -> None:
    """Re-fetch eloratings and drop the in-process rating caches. Tolerant: if the
    fetch fails (e.g. eloratings blocks the CI IP), keep the committed ratings.yaml
    and carry on — a stale-by-a-day rating still produces a sound re-prediction."""
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "refresh_ratings.py"), "--refresh"],
                       check=True)
        ratings._data.cache_clear()
        ratings._ratings.cache_clear()
    except subprocess.CalledProcessError as e:
        print(f"  warning: elo refresh failed ({e}); using committed data/ratings.yaml")


def fixture_match_id(fixture: Fixture) -> str:
    """Stable, deterministic DynamoDB key (Toqui's real id isn't known here)."""
    def slug(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum())
    return f"{fixture.group}-{slug(fixture.home)}-vs-{slug(fixture.away)}"


def is_editable(fx: ToquiFixture, now: datetime) -> bool:
    """Real (non-placeholder) teams and not yet locked."""
    if ratings.rating(fx.home) == ratings.DEFAULT_ELO or ratings.rating(fx.away) == ratings.DEFAULT_ELO:
        return False
    if not fx.lock_utc:
        return False
    return datetime.fromisoformat(fx.lock_utc.replace("Z", "+00:00")) > now


def should_submit(fx: ToquiFixture, now: datetime) -> bool:
    """Group picks are permanently locked — only knockout matches with real
    teams and an open lock are submission candidates."""
    return fx.stage != "group" and is_editable(fx, now)


def desired_pick(prefs, fx: ToquiFixture) -> str:
    override = prefs.override_for(fx.home, fx.away, fx.match_id)
    if override and override.score:
        return override.score
    return predict_fixture(Fixture(group=fx.stage, home=fx.home, away=fx.away),
                           scoring=scoring.for_toqui_stage(fx.stage)).pick


def run(live: bool) -> int:
    refresh_elo()
    prefs = preferences()
    now = datetime.now(timezone.utc)
    print(f"daily_update: {'LIVE' if live else 'DRY-RUN'} | {now.isoformat()}")

    changed = unchanged = failed = skipped_draw = 0
    log: list[dict] = []
    with ToquiClient(headless=True) as client:
        client.login()
        fixtures = client.fixtures()

        # 2-3: actual results -> tracker reports + knockout form corrections.
        summary = results.write_all(fixtures, now)
        print(f"  results: {summary['finished']} finished, {summary['points']} pts "
              f"earned, {summary['corrections']} team corrections "
              f"-> reports/results.md, data/form_adjustments.yaml")

        # 4: group reports (audit only — picks are locked) + DynamoDB snapshot.
        grp = scoring.for_stage("GRP")
        preds = [predict_fixture(f, scoring=grp) for f in group_matches()]
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / "group_stage.md").write_text(report.markdown_by_group(preds, TITLE) + "\n")
        (REPORTS / "group_stage.json").write_text(
            json.dumps([p.model_dump() for p in preds], indent=2, ensure_ascii=False))
        if live:
            for p in preds:
                put_prediction(fixture_match_id(p.fixture), p.model_dump())
        print(f"  reports rewritten ({len(preds)} matches)" + (" + DynamoDB snapshot" if live else ""))

        # 5: re-submit only changed picks for editable KNOCKOUT matches.
        for fx in fixtures:
            if not should_submit(fx, now):
                continue
            want = desired_pick(prefs, fx)
            h, a = (int(x) for x in want.replace(":", "-").split("-"))
            if h == a:
                # Knockout draws need a predictedAdvancerId we can't resolve yet
                # (Toqui team ids aren't in the flight). Loud skip, never silent.
                skipped_draw += 1
                print(f"  SKIP draw pick {want} (advancer unsupported): "
                      f"{fx.home} vs {fx.away} [{fx.stage}] — set an override "
                      f"or pick by hand")
                continue
            if fx.my_pick == (h, a):
                unchanged += 1
                continue
            old = f"{fx.my_pick[0]}-{fx.my_pick[1]}" if fx.my_pick else "none"
            print(f"  CHANGE {old} -> {want}: {fx.home} vs {fx.away} [{fx.stage}]")
            entry = {"match": f"{fx.home} vs {fx.away}", "old": old, "new": want,
                     "note": f"{fx.stage}: elo refresh / form / override"}
            if live:
                if client.submit_pick(fx.match_id, h, a, dry_run=False, fixtures=fixtures):
                    log_submission(fx.match_id, f"{want} (daily: was {old})",
                                   datetime.now(timezone.utc).isoformat())
                    log.append(entry)
                    changed += 1
                else:
                    # A wanted change that did NOT persist. Never drop it silently:
                    # record it as failed and fail the run so CI goes red.
                    failed += 1
                    print(f"  FAILED to submit: {fx.home} vs {fx.away} -> {want}")
                    log.append({**entry, "note": "FAILED to submit (see logs)"})
            else:
                changed += 1

    if live:
        changelog.record("daily-update", log)
    print(f"Done: {changed} {'changed' if live else 'would-change'}, "
          f"{unchanged} unchanged, {skipped_draw} draw-skipped, {failed} failed.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true", help="Apply changes (default dry-run).")
    return run(p.parse_args(argv).live)


if __name__ == "__main__":
    sys.exit(main())
