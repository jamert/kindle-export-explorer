"""Apply file I/O boundary policy to captured application stacks."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import FrameType

from .configuration import FileIOConfiguration
from .model import ApplicationFrame, Boundary, FileIOViolation


class ReadBoundaryEnforcer:
    def __init__(self, configuration: FileIOConfiguration) -> None:
        self._source_roots = configuration.source_roots
        self._allowed = configuration.allowed_reads
        self._violations: Counter[FileIOViolation] = Counter()

    @property
    def violations(self) -> Counter[FileIOViolation]:
        return self._violations.copy()

    def observe(self, target: str, frame: FrameType | None) -> None:
        frames = self._application_frames(frame)
        if not frames or any(item.boundary in self._allowed for item in frames):
            return
        self._violations[FileIOViolation(target, frames)] += 1

    def _application_frames(
        self,
        frame: FrameType | None,
    ) -> tuple[ApplicationFrame, ...]:
        frames: list[ApplicationFrame] = []
        while frame is not None:
            filename = Path(frame.f_code.co_filename)
            if any(filename.is_relative_to(root) for root in self._source_roots):
                module = frame.f_globals.get("__name__")
                module_name = module if isinstance(module, str) else "<unknown>"
                frames.append(
                    ApplicationFrame(
                        filename=str(filename),
                        lineno=frame.f_lineno,
                        boundary=Boundary(module_name, frame.f_code.co_qualname),
                    ),
                )
            frame = frame.f_back
        frames.reverse()
        return tuple(frames)
