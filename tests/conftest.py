import pytest


@pytest.fixture(autouse=True)
def isolate_kindle_export_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's export path out of tests and their subprocesses."""
    monkeypatch.delenv("KINDLE_EXPORT_PATH", raising=False)
