# Backtest — realism / risk-mode sweep vs actual WC2026 results

_Generated: 2026-07-20. One-off analysis, not part of the daily pipeline._

## TL;DR

- Only **`realism`** actually affects picks. `strategy.mode` (`safe | balanced | adaptive | aggressive`)
  and `comeback_aggression` in `config/preferences.yaml` are parsed but **never wired to any
  caller** — every script hardcodes `predict_fixture(..., risk="safe")`. The "adaptive" mode
  the pool has been running under all tournament was, in practice, always `safe`.
- Sweeping `realism` against the real scoring rules (`data/scoring.yaml`) and the model's own
  `best_pick()`, the best single global value is **`realism ≈ 0.25`**, vs. the `0.10` currently
  set — worth **+10 to +16 points** over the tournament (see caveat below on the knockout
  numbers). Gains plateau above `~0.30`; there's no benefit to pushing it higher.
- Simulating `risk="aggressive"` (always take the grid's single most-likely scoreline,
  ignore EV) is **worse everywhere** — 203 pts vs. 260-276 for any sane `realism`. Wiring
  `mode: aggressive` up would have cost points, not gained them.

## Method

`best_pick()` (`src/polla/predict.py`) only needs a probability grid (`lam_home`, `lam_away`)
and a decision rule (`risk`, `realism`) — it doesn't care how the grid was produced. So for
each of the 104 finished matches (`reports/results.json`) this reruns the real
`poisson`/`predict` code, holding the grid fixed and sweeping only the decision rule:

- **Group stage (72 matches):** grid rebuilt from the `expected_goals_home/away` already
  stored per-match in `reports/group_stage.json`. Reproduces that file's own recorded pick in
  71/72 matches (the 72nd is a coin-flip EV tie sensitive to 2-decimal rounding) — the
  reconstruction is faithful.
- **Knockout stage (32 matches):** no historical per-date grid is persisted anywhere in the
  repo (`data/ratings.yaml` is overwritten daily, not versioned), so these are rebuilt from
  **today's** `ratings.effective_diff()` — i.e. Elo that has since absorbed the very results
  being "predicted."

**Caveat — knockout numbers are an upper bound, not a replay.** Because Elo already knows who
won, late-knockout picks (semi-final, third-place, final) get a lookahead boost no live
config could have had — e.g. at `realism=0.00` the model now picks the final's `1-0` exactly,
which it did not pick live. Group-stage numbers aren't leakage-free either (ratings.yaml
drifts daily) but are far less affected. Treat the **relative ranking** of `realism` values as
the trustworthy signal; treat absolute knockout point totals as optimistic.

## Group stage — realism sweep (72 matches, low leakage)

| realism | points | vs. current (0.10) |
|---|---|---|
| 0.00 – 0.05 | 66 | −6 |
| **0.10** | **72** | current setting |
| 0.15 | 69 | −3 |
| **0.20** | **72** | tied-best |
| 0.25 | 69 | −3 |
| 0.30 | 66 | −6 |
| ≥0.40 | 60 | −12 (plateau) |
| `aggressive` | 71 | −1 |

Current `0.10` is already at the group-stage optimum (tied with `0.20`). Nothing to change here.

## Knockout stage — realism sweep (32 matches, leakage-prone, read as upper bound)

| realism | points | vs. current (0.10) |
|---|---|---|
| 0.00 – 0.05 | 194 | −3 |
| **0.10** | **197** | current setting |
| 0.15 – 0.20 | 202 | +5 |
| **0.25 – 1.00** | **207** | **+10 (plateau, flat above 0.25)** |
| `aggressive` | 132 | −65 |

## Combined (all 104 matches)

| realism | points |
|---|---|
| 0.00 – 0.05 | 260 |
| 0.10 (current) | 269 |
| 0.15 | 271 |
| 0.20 | 274 |
| **0.25** | **276 (best)** |
| 0.30 | 273 |
| 0.35 | 270 |
| ≥0.40 | 267 (plateau) |
| `aggressive` | 203 |

The global optimum (`0.25`) is pulled up from the group-stage optimum (`0.10`/`0.20`) by the
knockout stage, where scoring weights are much higher (outcome/exact up to 20/30 vs. 1/3 in
groups) — a small knockout-only gain outweighs a small group-stage-only loss. The code only
supports one global `realism`, so this is the actionable single-number recommendation; a
per-stage knob (group stays `0.10`, knockout goes to `0.25`) would do slightly better if ever
worth adding, but isn't built.

## Case study: the final, and what `realism` actually trades away

Spain 1-0 Argentina. With today's Elo (diff +70 to Spain), pure EV (`realism=0.00`) picks
**`1-0`** — exact. Raise `realism` to `0.10` (today's setting) and the pick flips to **`2-1`**,
because a competitive-but-not-huge Elo edge makes a both-teams-score line look "realistic
enough" to take within the EV band — which is exactly the historical live pick, and it only
got the outcome point, not the exact bonus. This is the mechanism `realism` trades on
generally (see `best_pick()` docstring): it buys more true-to-life scorelines in exchange for
occasionally giving up a clean-sheet exact hit like this one. Net positive across 104 matches,
net negative on this specific one.

## Dead config, for the record

`config/preferences.yaml`'s `strategy.mode` and `comeback_aggression` describe a
safe/balanced/adaptive/aggressive spectrum that reads as live behavior, but
`src/polla/predict.py::best_pick` only branches on `risk == "aggressive"`, and no caller
(`jobs/daily_update.py`, `jobs/pre_lock_submit.py`, `scripts/predict_group_stage.py`, …) ever
passes anything but the default `risk="safe"`. If this is worth keeping as a real feature,
`mode` needs to actually select `risk` before calling `predict_fixture`; if not, it's worth
deleting from the config to stop describing behavior the code doesn't have.
