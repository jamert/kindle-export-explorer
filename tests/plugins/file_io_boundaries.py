"""Development-time enforcement of configured file I/O boundaries."""

from __future__ import annotations

import builtins
import inspect
import linecache
import os
import sys
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FrameType
from typing import cast

import pytest
from _pytest.terminal import TerminalReporter


@dataclass(frozen=True)
class Boundary:
    module: str
    qualname: str

    @classmethod
    def parse(cls, value: str) -> Boundary:
        module, separator, qualname = value.partition(":")
        if not separator or not module or not qualname:
            raise ValueError(
                f"invalid boundary {value!r}; expected '<module>:<qualified name>'"
            )
        return cls(module, qualname)


@dataclass(frozen=True)
class ApplicationFrame:
    filename: str
    lineno: int
    module: str
    qualname: str

    @property
    def boundary(self) -> Boundary:
        return Boundary(self.module, self.qualname)


@dataclass(frozen=True)
class FileIOViolation:
    path: str
    frames: tuple[ApplicationFrame, ...]


@dataclass(frozen=True)
class FileIOConfiguration:
    source_roots: tuple[Path, ...]
    allowed_reads: frozenset[Boundary]
    allowed_writes: frozenset[Boundary] = frozenset()

    @classmethod
    def load(cls, path: Path) -> FileIOConfiguration:
        try:
            with path.open("rb") as stream:
                value: object = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"cannot read file I/O configuration {path}: {exc}") from exc

        table = _string_keyed_table(value, str(path))
        tool = _string_keyed_table(table.get("tool"), f"{path}: tool")
        project = _string_keyed_table(
            tool.get("kindle-export-explorer"),
            f"{path}: tool.kindle-export-explorer",
        )
        location = f"{path}: tool.kindle-export-explorer.file-io-boundaries"
        file_io = _string_keyed_table(project.get("file-io-boundaries"), location)
        roots = _string_list(file_io.get("source-roots"), f"{location}.source-roots")
        reads = _string_list(file_io.get("allowed-reads"), f"{location}.allowed-reads")
        writes_value = file_io.get("allowed-writes")
        writes = (
            []
            if writes_value is None
            else _string_list(writes_value, f"{location}.allowed-writes")
        )
        if not roots:
            raise ValueError(f"{path}: source-roots must not be empty")

        base = path.parent.resolve()
        return cls(
            source_roots=tuple((base / root).resolve() for root in roots),
            allowed_reads=frozenset(Boundary.parse(value) for value in reads),
            allowed_writes=frozenset(Boundary.parse(value) for value in writes),
        )


class FileIOBoundaryPlugin:
    """Collect file reads made outside configured application boundaries."""

    def __init__(self, configuration: FileIOConfiguration) -> None:
        self._configuration = configuration
        self._active = False
        self._installed = False
        self._monitoring_tool_id: int | None = None
        self._violations: Counter[FileIOViolation] = Counter()

    @classmethod
    def from_path(cls, path: Path) -> FileIOBoundaryPlugin:
        return cls(FileIOConfiguration.load(path))

    @property
    def violations(self) -> Counter[FileIOViolation]:
        return self._violations.copy()

    def install(self) -> None:
        if not self._installed:
            sys.addaudithook(self._audit)
            self._installed = True
        self._install_call_monitoring()
        self._active = True

    def disable(self) -> None:
        # Python audit hooks cannot be removed, so leave an inert hook behind.
        self._active = False
        if self._monitoring_tool_id is not None:
            tool_id = self._monitoring_tool_id
            sys.monitoring.set_events(tool_id, 0)
            sys.monitoring.register_callback(tool_id, sys.monitoring.events.CALL, None)
            sys.monitoring.free_tool_id(tool_id)
            self._monitoring_tool_id = None

    def _install_call_monitoring(self) -> None:
        if self._monitoring_tool_id is not None:
            return
        tool_id = next(
            (candidate for candidate in range(6) if sys.monitoring.get_tool(candidate) is None),
            None,
        )
        if tool_id is None:
            raise RuntimeError("cannot monitor stdin reads: no sys.monitoring tool ID is free")
        sys.monitoring.use_tool_id(tool_id, "file-io-boundary-guard")
        sys.monitoring.register_callback(
            tool_id,
            sys.monitoring.events.CALL,
            self._monitor_call,
        )
        sys.monitoring.set_events(tool_id, sys.monitoring.events.CALL)
        self._monitoring_tool_id = tool_id

    def _audit(self, event: str, arguments: tuple[object, ...]) -> None:
        if not self._active:
            return
        if event == "open":
            if not open_event_reads(arguments):
                return
            target = _display_path(arguments[0]) if arguments else "<unknown>"
        elif event in {"os.listdir", "os.scandir"}:
            target = _display_path(arguments[0]) if arguments else "<unknown>"
        elif event == "builtins.input":
            target = "stdin"
        else:
            return

        current_frame = inspect.currentframe()
        self._record_read(target, current_frame.f_back if current_frame else None)

    def _monitor_call(
        self,
        code: CodeType,
        instruction_offset: int,
        callable_object: object,
        arg0: object,
    ) -> None:
        del code, instruction_offset
        if not self._active or not call_reads_stdin(callable_object, arg0):
            return
        current_frame = inspect.currentframe()
        self._record_read("stdin", current_frame.f_back if current_frame else None)

    def _record_read(self, target: str, frame: FrameType | None) -> None:
        frames = self._application_frames(frame)
        if not frames:
            return
        if any(
            item.boundary in self._configuration.allowed_reads for item in frames
        ):
            return
        self._violations[FileIOViolation(target, frames)] += 1

    def _application_frames(
        self, frame: FrameType | None
    ) -> tuple[ApplicationFrame, ...]:
        frames: list[ApplicationFrame] = []
        while frame is not None:
            filename = Path(frame.f_code.co_filename)
            if any(
                filename.is_relative_to(root)
                for root in self._configuration.source_roots
            ):
                module_value = frame.f_globals.get("__name__")
                module = module_value if isinstance(module_value, str) else "<unknown>"
                frames.append(
                    ApplicationFrame(
                        str(filename),
                        frame.f_lineno,
                        module,
                        frame.f_code.co_qualname,
                    )
                )
            frame = frame.f_back
        frames.reverse()
        return tuple(frames)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        del session
        # Collection imports application modules. Enforce the boundary while tests
        # execute, after those import-related file reads have completed.
        self.install()

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(
        self,
        session: pytest.Session,
        exitstatus: int | pytest.ExitCode,
    ) -> None:
        del exitstatus
        self.disable()
        if self._violations:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(
        self,
        terminalreporter: TerminalReporter,
        exitstatus: pytest.ExitCode,
        config: pytest.Config,
    ) -> None:
        del exitstatus
        if not self._violations:
            return
        terminalreporter.section("File I/O boundary violations", red=True, bold=True)
        for line in violation_report_lines(
            self._violations,
            verbose=config.get_verbosity() > 0,
        ):
            terminalreporter.write_line(line)


_STDIN_READ_METHODS = {
    "__next__",
    "read",
    "read1",
    "readall",
    "readinto",
    "readinto1",
    "readline",
    "readlines",
}


def call_reads_stdin(callable_object: object, arg0: object) -> bool:
    """Return whether a monitored call reads directly from standard input."""
    if callable_object is os.read:
        return arg0 == 0
    if callable_object is builtins.next:
        return _is_standard_input(arg0)

    name_value = getattr(callable_object, "__name__", None)
    if not isinstance(name_value, str) or name_value not in _STDIN_READ_METHODS:
        return False

    receiver = getattr(callable_object, "__self__", None)
    return _is_standard_input(receiver) or _is_standard_input(arg0)


def _is_standard_input(candidate: object) -> bool:
    stdin = sys.stdin
    return candidate is stdin or candidate is getattr(stdin, "buffer", None)


def open_event_reads(arguments: Sequence[object]) -> bool:
    """Return whether an ``open`` audit event can read from its target."""
    mode = arguments[1] if len(arguments) > 1 else None
    if isinstance(mode, str):
        return "r" in mode or "+" in mode

    flags = arguments[2] if len(arguments) > 2 else None
    if isinstance(flags, int):
        access_mode = flags & os.O_ACCMODE
        return access_mode in {os.O_RDONLY, os.O_RDWR}
    return False


def _display_path(value: object) -> str:
    if isinstance(value, (str, bytes)):
        return repr(value)
    if isinstance(value, int):
        return f"file descriptor {value}"
    return repr(value)


def violation_report_lines(
    violations: Mapping[FileIOViolation, int],
    *,
    verbose: bool,
) -> list[str]:
    """Format either a symbol summary or full paths and application stacks."""
    if not verbose:
        frame_counts: Counter[Boundary] = Counter()
        for violation, count in violations.items():
            for frame in violation.frames:
                frame_counts[frame.boundary] += count
        total = sum(violations.values())
        lines = [f"{total} read events occurred outside allowed boundaries:"]
        for boundary, count in sorted(
            frame_counts.items(),
            key=lambda item: (-item[1], item[0].module, item[0].qualname),
        ):
            lines.append(f"  {count:>4}  {boundary.module}:{boundary.qualname}")
        lines.append("Run pytest -v to show individual paths and application stacks.")
        return lines

    lines: list[str] = []
    for violation, count in violations.items():
        suffix = f" ({count} occurrences)" if count > 1 else ""
        lines.append(f"read: {violation.path}{suffix}")
        lines.append("Application stack (most recent call last):")
        lines.extend(_standard_stack_lines(violation.frames))
        lines.append("  No allowed read boundary was present.")
    return lines


def _standard_stack_lines(frames: Sequence[ApplicationFrame]) -> list[str]:
    lines: list[str] = []
    for frame in frames:
        lines.append(
            f'  File "{frame.filename}", line {frame.lineno}, in {frame.qualname}'
        )
        source = linecache.getline(frame.filename, frame.lineno).strip()
        if source:
            lines.append(f"    {source}")
    return lines


def _string_keyed_table(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{location}: expected a table")
    table = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in table):
        raise ValueError(f"{location}: expected string keys")
    return {str(key): item for key, item in table.items()}


def _string_list(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{location}: expected an array of strings")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{location}: expected an array of strings")
    return [item for item in items if isinstance(item, str)]
