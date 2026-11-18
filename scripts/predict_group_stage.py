"""Predict all 72 group-stage matches (locks at tournament start)."""
from __future__ import annotations

import json

from polla import report, scoring
from polla.config import REPORTS
from polla.fixtures import group_matches
from polla.predict import predict_fixture

TITLE = "La Polla del Miedo — Group stage (all 72 matches)"


def main() -> None:
    grp = scoring.for_stage("GRP")
    preds = [predict_fixture(f, scoring=grp) for f in group_matches()]

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "group_stage.md").write_text(report.markdown_by_group(preds, TITLE) + "\n")
    (REPORTS / "group_stage.json").write_text(
        json.dumps([p.model_dump() for p in preds], indent=2, ensure_ascii=False)
    )
    print(f"Predicted {len(preds)} group matches -> reports/group_stage.md")
    print(f"Group-stage picks lock: {scoring.lock_time('GRP')}")


if __name__ == "__main__":
    main()
