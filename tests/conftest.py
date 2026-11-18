import pytest

from polla import changelog


@pytest.fixture(autouse=True)
def _isolate_changelog(tmp_path, monkeypatch):
    """Never let a test write the real reports/CHANGELOG.md."""
    monkeypatch.setattr(changelog, "REPORTS", tmp_path)
    monkeypatch.setattr(changelog, "PATH", tmp_path / "CHANGELOG.md")
