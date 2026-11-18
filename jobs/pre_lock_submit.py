#!/usr/bin/env python3
"""Pre-lock submit: for every Toqui match locking soon and not yet submitted,
compute the FINAL pick and submit it via Toqui.

THE FINAL PICK PRECEDENCE (highest wins):
    1. preferences `skip`     -> never submitted by this job (you enter by hand)
    2. preferences `override` -> verbatim scoreline beats the model
    3. model prediction       -> predict_fixture() pick

THE DRY-RUN / LIVE GATE:
    Default is DRY-RUN. `submit_pick(..., dry_run=True)` does NOT write to Toqui.
    Only `--live` flips dry_run to False and actually submits. The GitHub
    workflow only passes --live when a human sets the `live` dispatch input to
    true, so a stray cron run can never submit for real.

Run (dry-run, the default):
    uv run python jobs/pre_lock_submit.py --within-hours 24

Run (real submit — deliberate):
    uv run python jobs/pre_lock_submit.py --within-hours 24 --live
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from polla import changelog, scoring
from polla.config import preferences
from polla.models import Fixture
from polla.predict import predict_fixture
from polla.store import log_submission
from polla.toqui import ToquiClient, ToquiFixture


def _key(name: str) -> str:
    """Match the normalization used by Preferences (alnum, lowercased)."""
    return "".join(c for c in name.lower().strip() if c.isalnum())


def is_skipped(prefs, fixture: ToquiFixture) -> bool:
    """A match is skipped if either 'Home vs Away' or a bare team name in the
    `skip` list matches this fixture."""
    home, away = _key(fixture.home), _key(fixture.away)
    pair = (home, away)
    for entry in prefs.skip:
        if not entry:
            continue
        low = entry.lower()
        if " vs " in low:
            h, a = low.split(" vs ", 1)
            if (_key(h), _key(a)) == pair:
                return True
        elif _key(entry) in (home, away):
            return True
    return False


def final_pick(prefs, fixture: ToquiFixture) -> tuple[str, str]:
    """Return (scoreline, source). A preferences override beats the model.
    Scoring weights follow the fixture's stage (R32 pays 3/5, final 20/30...)."""
    override = prefs.override_for(fixture.home, fixture.away, fixture.match_id)
    if override and override.score:
        return override.score, "override"
    # The model's Fixture.group is informational here; carry the Toqui stage.
    model_fixture = Fixture(group=fixture.stage, home=fixture.home, away=fixture.away)
    pred = predict_fixture(model_fixture, scoring=scoring.for_toqui_stage(fixture.stage))
    return pred.pick, "model"


def _parse_score(score: str) -> tuple[int, int]:
    h, a = score.replace(":", "-").split("-")
    return int(h), int(a)


def within_window(fixture: ToquiFixture, now: datetime, within_hours: float) -> bool:
    """True if the match locks within the next `within_hours` and hasn't locked
    already. lock_utc may be an ISO string (e.g. '2026-06-11T19:00:00.000Z')."""
    lock = fixture.lock_utc
    if lock is None:
        return False
    if isinstance(lock, str):
        lock = datetime.fromisoformat(lock.replace("Z", "+00:00"))
    if lock.tzinfo is None:
        lock = lock.replace(tzinfo=timezone.utc)
    delta_hours = (lock - now).total_seconds() / 3600.0
    return 0 <= delta_hours <= within_hours


def run(within_hours: float, live: bool) -> int:
    prefs = preferences()
    now = datetime.now(timezone.utc)
    dry_run = not live

    mode = "LIVE SUBMIT" if live else "DRY-RUN"
    print(f"pre_lock_submit: {mode} | window={within_hours}h | now={now.isoformat()}")

    submitted = skipped = already = out_of_window = failed = 0
    log: list[dict] = []

    with ToquiClient(headless=True) as client:
        client.login()
        fixtures = client.fixtures()

        for fx in fixtures:
            label = f"{fx.home} vs {fx.away} [{fx.stage}] lock={fx.lock_utc}"

            if fx.stage == "group":
                continue  # group stage is permanently locked — never touch it
            if fx.my_pick is not None:
                already += 1
                continue
            if not within_window(fx, now, within_hours):
                out_of_window += 1
                continue
            if is_skipped(prefs, fx):
                print(f"  SKIP (preferences.skip): {label}")
                skipped += 1
                continue

            score, source = final_pick(prefs, fx)
            home_goals, away_goals = _parse_score(score)
            if home_goals == away_goals:
                # Knockout draws need a predictedAdvancerId we can't resolve yet.
                print(f"  SKIP draw pick {score} (advancer unsupported — pick by "
                      f"hand or set an override): {label}")
                skipped += 1
                continue

            ok = client.submit_pick(
                fx.match_id, home_goals, away_goals, dry_run=dry_run
            )
            tag = "submitted" if (ok and live) else ("would submit" if ok else "FAILED")
            print(f"  {tag} {score} ({source}): {label}")

            if ok:
                ts = datetime.now(timezone.utc).isoformat()
                # Record dry-runs too, tagged, so we have an audit trail of intent.
                logged_score = score if live else f"{score} (dry-run)"
                log_submission(fx.match_id, logged_score, ts)
                if live:
                    log.append({"match": f"{fx.home} vs {fx.away}", "old": "none",
                                "new": score, "note": source})
                submitted += 1
            elif live:
                # Intended submit that didn't persist — surface it, don't drop it.
                failed += 1
                log.append({"match": f"{fx.home} vs {fx.away}", "old": "none",
                            "new": score, "note": "FAILED to submit (see logs)"})

    if live:
        changelog.record("pre-lock-submit", log)
    print(
        f"Done: {submitted} {'submitted' if live else 'would-submit'}, "
        f"{skipped} skipped, {already} already-picked, "
        f"{out_of_window} outside window, {failed} failed."
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--within-hours", type=float, default=24,
        help="Only consider matches locking within this many hours (default 24).",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Actually submit to Toqui. Without this flag the job is DRY-RUN.",
    )
    args = parser.parse_args(argv)
    return run(args.within_hours, args.live)


if __name__ == "__main__":
    sys.exit(main())
