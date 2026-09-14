import io
import sys
from pathlib import Path

import pytest

from tests.plugins.file_io_boundaries import (
    ApplicationFrame,
    Boundary,
    FileIOBoundaryPlugin,
    FileIOConfiguration,
    FileIOViolation,
    open_event_reads,
    violation_report_lines,
)


def _read_under_allowed_boundary(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_allows_reads_when_configured_boundary_is_on_stack(tmp_path: Path) -> None:
    path = tmp_path / "allowed.txt"
    path.write_text("allowed", encoding="utf-8")
    guard = FileIOBoundaryPlugin(
        FileIOConfiguration(
            source_roots=(Path(__file__).parent,),
            allowed_reads=frozenset(
                {
                    Boundary(
                        __name__,
                        _read_under_allowed_boundary.__qualname__,
                    )
                }
            ),
        )
    )

    guard.install()
    try:
        assert _read_under_allowed_boundary(path) == "allowed"
    finally:
        guard.disable()

    assert not guard.violations


def test_aggregates_reads_without_configured_boundary(tmp_path: Path) -> None:
    path = tmp_path / "forbidden.txt"
    path.write_text("forbidden", encoding="utf-8")
    guard = FileIOBoundaryPlugin(
        FileIOConfiguration(
            source_roots=(Path(__file__).parent,),
            allowed_reads=frozenset(),
        )
    )

    guard.install()
    try:
        assert path.read_text(encoding="utf-8") == "forbidden"
        assert path.read_text(encoding="utf-8") == "forbidden"
    finally:
        guard.disable()

    assert sum(guard.violations.values()) == 2
    assert {violation.target for violation in guard.violations} == {repr(str(path))}


def test_configuration_allows_omitting_write_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        """
[tool.kindle-export-explorer.file-io-boundaries]
source-roots = ["src"]
allowed-reads = []
""",
        encoding="utf-8",
    )

    configuration = FileIOConfiguration.load(path)

    assert configuration.allowed_writes == frozenset()


def test_monitors_arbitrary_standard_input_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("abc\ndef\n"))
    guard = FileIOBoundaryPlugin(
        FileIOConfiguration(
            source_roots=(Path(__file__).parent,),
            allowed_reads=frozenset(),
        )
    )

    guard.install()
    try:
        assert sys.stdin.read(1) == "a"
        assert sys.stdin.readline() == "bc\n"
        assert next(sys.stdin) == "def\n"
        monkeypatch.setattr(sys, "stdin", io.StringIO("ghi\n"))
        assert input() == "ghi"
    finally:
        guard.disable()

    assert sum(guard.violations.values()) == 4
    assert {violation.target for violation in guard.violations} == {"stdin"}


def test_classifies_open_modes_that_can_read() -> None:
    assert open_event_reads(("file", "r", 0))
    assert open_event_reads(("file", "rb", 0))
    assert open_event_reads(("file", "w+", 0))
    assert not open_event_reads(("file", "w", 0))
    assert not open_event_reads(("file", "a", 0))


def test_summarizes_symbols_unless_verbose(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("read_data()\n", encoding="utf-8")
    frame = ApplicationFrame(
        filename=str(source),
        lineno=1,
        boundary=Boundary("package.module", "read_data"),
    )
    violation = FileIOViolation("'secret.json'", (frame,))

    summary = violation_report_lines({violation: 3}, verbose=False)
    assert summary == [
        "3 read events occurred outside allowed boundaries:",
        "     3  package.module:read_data",
        "Run pytest -v to show individual paths and application stacks.",
    ]
    assert "secret.json" not in "\n".join(summary)

    details = violation_report_lines({violation: 3}, verbose=True)
    assert "read: 'secret.json' (3 occurrences)" in details
    assert any(f'File "{source}", line 1, in read_data' in line for line in details)
