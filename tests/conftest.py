from pathlib import Path

import pytest

from tests.plugins.file_io_boundaries import FileIOBoundaryPlugin

_FILE_IO_PLUGIN = FileIOBoundaryPlugin.from_path(
    Path(__file__).parents[1] / "pyproject.toml",
)


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(_FILE_IO_PLUGIN, "file-io-boundary-guard")


@pytest.fixture(autouse=True)
def isolate_application_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep developer export and resolution paths out of tests."""
    monkeypatch.delenv("KINDLE_EXPORT_PATH", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
