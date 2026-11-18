"""Predict the opening match of each of the 12 groups."""
from __future__ import annotations

import json

from polla import report
from polla.config import REPORTS
from polla.fixtures import first_matches
from polla.predict import predict_fixture

TITLE = "La Polla del Miedo — Group openers (initial predictions)"


def main() -> None:
    preds = [predict_fixture(f) for f in first_matches()]

    REPORTS.mkdir(exist_ok=True)
    md = report.markdown(preds, TITLE)
    (REPORTS / "first_matches.md").write_text(md + "\n")
    (REPORTS / "first_matches.json").write_text(
        json.dumps([p.model_dump() for p in preds], indent=2, ensure_ascii=False)
    )
    print(md)
    print(f"\nWrote {REPORTS/'first_matches.md'} and first_matches.json")


if __name__ == "__main__":
    main()
