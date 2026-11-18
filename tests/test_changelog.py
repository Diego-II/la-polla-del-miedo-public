from __future__ import annotations

from polla import changelog


def test_changelog_records_changes_and_heartbeat(tmp_path, monkeypatch):
    p = tmp_path / "CHANGELOG.md"
    monkeypatch.setattr(changelog, "REPORTS", tmp_path)
    monkeypatch.setattr(changelog, "PATH", p)

    changelog.record("pre-lock-submit", [
        {"match": "Spain vs Uruguay", "old": "none", "new": "2-1", "note": "model"},
    ])
    changelog.record("daily-update", [])  # heartbeat

    text = p.read_text()
    assert text.count("# Prediction changelog") == 1  # header written once, de-duped
    assert "Spain vs Uruguay" in text and "2-1" in text
    assert "_No prediction changes this run._" in text
    assert text.count("\n## ") == 2                   # one section per run
    # newest first: the later-recorded daily-update appears above pre-lock-submit
    assert text.index("daily-update") < text.index("pre-lock-submit")
