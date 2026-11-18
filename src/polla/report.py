from __future__ import annotations

from .config import Preferences, preferences
from .models import Prediction


def _pct(x: float) -> str:
    return f"{round(x * 100):>3d}%"


def _row(p: Prediction, prefs: Preferences) -> str:
    f = p.fixture
    bell = " 🔔" if (prefs.is_interesting_team(f.home) or prefs.is_interesting_team(f.away)) else ""
    wdl = f"{_pct(p.p_home)}/{_pct(p.p_draw)}/{_pct(p.p_away)}"
    tops = ", ".join(f"{s.score} ({_pct(s.prob)})" for s in p.top_scorelines[:3])
    pick = f"**{p.pick}**" + ("  ⚙️" if p.overridden else "")
    return f"| {f.label}{bell} | {f.date} | {pick} | {wdl} | {tops} |"


def markdown_by_group(predictions: list[Prediction], title: str,
                      prefs: Preferences | None = None) -> str:
    prefs = prefs or preferences()
    by_group: dict[str, list[Prediction]] = {}
    for p in predictions:
        by_group.setdefault(p.fixture.group, []).append(p)
    lines = [f"# {title}", "", "🔔 = involves one of your teams · ⚙️ = manual override", ""]
    for g in sorted(by_group):
        lines += [f"## Group {g}", "",
                  "| Match | Date | Pick | Home/Draw/Away | Top scorelines |",
                  "|-------|------|------|----------------|----------------|"]
        lines += [_row(p, prefs) for p in by_group[g]]
        lines.append("")
    lines.append("_Group scoring +1 outcome / +3 exact (additive); pick maximizes expected points._")
    return "\n".join(lines)


def markdown(predictions: list[Prediction], title: str) -> str:
    lines = [f"# {title}", ""]
    lines += [
        "| Grp | Match | Date | Pick | Most likely | Home/Draw/Away | xG | Top scorelines |",
        "|-----|-------|------|------|-------------|----------------|----|----------------|",
    ]
    for p in predictions:
        f = p.fixture
        wdl = f"{_pct(p.p_home)}/{_pct(p.p_draw)}/{_pct(p.p_away)}"
        xg = f"{p.expected_goals_home:.1f}–{p.expected_goals_away:.1f}"
        tops = ", ".join(f"{s.score} ({_pct(s.prob)})" for s in p.top_scorelines[:3])
        pick = f"**{p.pick}**" + ("  ⚙️" if p.overridden else "")
        lines.append(
            f"| {f.group} | {f.label} | {f.date} | {pick} | {p.most_likely} | {wdl} | {xg} | {tops} |"
        )
    lines += ["", "_Pick = recommended scoreline to submit. ⚙️ = manual override from preferences.yaml._",
              "_Dixon-Coles Poisson anchored to Elo. Group scoring +1 outcome / +3 exact (additive); pick maximizes expected points._"]
    return "\n".join(lines)
