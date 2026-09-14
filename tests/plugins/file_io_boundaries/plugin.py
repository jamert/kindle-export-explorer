"""Pytest lifecycle integration for file I/O boundary enforcement."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Protocol

import pytest

from .configuration import FileIOConfiguration
from .enforcement import ReadBoundaryEnforcer
from .model import FileIOViolation
from .reporting import violation_report_lines
from .runtime import RuntimeReadMonitor


class TerminalReporter(Protocol):
    def section(
        self,
        title: str,
        sep: str = "=",
        *,
        red: bool = False,
        bold: bool = False,
    ) -> None: ...

    def write_line(self, line: str) -> None: ...


class FileIOBoundaryPlugin:
    """Connect runtime file I/O boundary enforcement to the pytest lifecycle."""

    def __init__(self, configuration: FileIOConfiguration) -> None:
        self._enforcer = ReadBoundaryEnforcer(configuration)
        self._monitor = RuntimeReadMonitor(self._enforcer.observe)

    @classmethod
    def from_path(cls, path: Path) -> FileIOBoundaryPlugin:
        return cls(FileIOConfiguration.load(path))

    @property
    def violations(self) -> Counter[FileIOViolation]:
        return self._enforcer.violations

    def install(self) -> None:
        self._monitor.start()

    def disable(self) -> None:
        self._monitor.stop()

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        del session
        # Collection imports application modules. Begin after those reads finish.
        self.install()

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(
        self,
        session: pytest.Session,
        exitstatus: int | pytest.ExitCode,
    ) -> None:
        del exitstatus
        self.disable()
        if self.violations:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(
        self,
        terminalreporter: TerminalReporter,
        exitstatus: pytest.ExitCode,
        config: pytest.Config,
    ) -> None:
        del exitstatus
        violations = self.violations
        if not violations:
            return
        terminalreporter.section("File I/O boundary violations", red=True, bold=True)
        for line in violation_report_lines(
            violations,
            verbose=config.get_verbosity() > 0,
        ):
            terminalreporter.write_line(line)
