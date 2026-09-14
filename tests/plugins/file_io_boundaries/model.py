"""Value objects shared by file I/O boundary enforcement components."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
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

    def __str__(self) -> str:
        return f"{self.module}:{self.qualname}"


@dataclass(frozen=True)
class ApplicationFrame:
    filename: str
    lineno: int
    boundary: Boundary


@dataclass(frozen=True)
class FileIOViolation:
    target: str
    frames: tuple[ApplicationFrame, ...]
