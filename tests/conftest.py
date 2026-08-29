from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_application_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep developer export and resolution paths out of tests."""
    monkeypatch.delenv("KINDLE_EXPORT_PATH", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
