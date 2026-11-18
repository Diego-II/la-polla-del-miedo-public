"""Prediction changelog (reports/CHANGELOG.md). Each live Actions run PREPENDS a
section (newest at the top) recording what it changed — or a heartbeat if nothing
changed — so the git history doubles as an audit trail."""
from __future__ import annotations

from datetime import datetime, timezone

from .config import REPORTS

PATH = REPORTS / "CHANGELOG.md"
_HEADER = "# Prediction changelog\n\nNewest entries at the top. One section per live run.\n"


def record(source: str, changes: list[dict], now: datetime | None = None) -> None:
    """changes: list of {match, old, new, note}. Empty -> a 'no changes' heartbeat.
    The new section is prepended directly under the header."""
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    rows = [f"## {ts} — {source}", ""]
    if changes:
        rows += ["| Match | Old | New | Note |", "|---|---|---|---|"]
        rows += [f"| {c['match']} | {c.get('old', '—')} | {c['new']} | {c.get('note', '')} |"
                 for c in changes]
    else:
        rows.append("_No prediction changes this run._")
    block = "\n".join(rows) + "\n"

    REPORTS.mkdir(exist_ok=True)
    existing = ""
    if PATH.exists():
        txt = PATH.read_text()
        i = txt.find("## ")            # keep everything from the first section onward
        existing = txt[i:] if i != -1 else ""
    PATH.write_text(f"{_HEADER}\n{block}\n{existing}")
