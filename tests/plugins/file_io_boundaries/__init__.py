"""Repository-local pytest plugin for enforcing file I/O boundaries."""

from .configuration import FileIOConfiguration
from .model import ApplicationFrame, Boundary, FileIOViolation
from .plugin import FileIOBoundaryPlugin
from .reporting import violation_report_lines
from .runtime import call_reads_stdin, open_event_reads

__all__ = [
    "ApplicationFrame",
    "Boundary",
    "FileIOBoundaryPlugin",
    "FileIOConfiguration",
    "FileIOViolation",
    "call_reads_stdin",
    "open_event_reads",
    "violation_report_lines",
]
