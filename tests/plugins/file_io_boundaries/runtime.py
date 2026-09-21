"""Observe filesystem and standard-input reads at runtime."""

from __future__ import annotations

import builtins
import inspect
import os
import sys
from collections.abc import Callable, Sequence
from types import CodeType, FrameType

_TOOL_NAME = "file-io-boundary-guard"
_DIRECTORY_READ_EVENTS = frozenset({"os.listdir", "os.scandir"})
_STDIN_READ_METHODS = frozenset(
    {
        "__next__",
        "read",
        "read1",
        "readall",
        "readinto",
        "readinto1",
        "readline",
        "readlines",
    },
)


type ReadObserver = Callable[[str, FrameType | None], None]
type CallEventCallback = Callable[[CodeType, int, object, object], None]


class CallMonitoringTool:
    """Own one temporary ``sys.monitoring`` tool registration."""

    def __init__(self, callback: CallEventCallback) -> None:
        self._callback = callback
        self._tool_id: int | None = None

    def start(self) -> None:
        if self._tool_id is not None:
            return
        tool_id = self._claim_tool_id()
        try:
            sys.monitoring.register_callback(
                tool_id,
                sys.monitoring.events.CALL,
                self._callback,
            )
            sys.monitoring.set_events(tool_id, sys.monitoring.events.CALL)
        except Exception:
            sys.monitoring.free_tool_id(tool_id)
            raise
        self._tool_id = tool_id

    def stop(self) -> None:
        if self._tool_id is None:
            return
        tool_id, self._tool_id = self._tool_id, None
        sys.monitoring.set_events(tool_id, 0)
        sys.monitoring.register_callback(tool_id, sys.monitoring.events.CALL, None)
        sys.monitoring.free_tool_id(tool_id)

    @staticmethod
    def _claim_tool_id() -> int:
        candidates = range(sys.monitoring.OPTIMIZER_ID + 1)
        tool_id = next(
            (
                candidate
                for candidate in candidates
                if sys.monitoring.get_tool(candidate) is None
            ),
            None,
        )
        if tool_id is None:
            raise RuntimeError(
                "cannot monitor stdin reads: no sys.monitoring tool ID is free",
            )
        sys.monitoring.use_tool_id(tool_id, _TOOL_NAME)
        return tool_id


class RuntimeReadMonitor:
    """Translate runtime audit and call events into logical read events."""

    def __init__(self, observer: ReadObserver) -> None:
        self._observer = observer
        self._call_monitor = CallMonitoringTool(self._monitor_call)
        self._audit_hook_installed = False
        self._active = False

    def start(self) -> None:
        if not self._audit_hook_installed:
            # Audit hooks cannot be removed. This hook remains inert after stop().
            sys.addaudithook(self._audit)
            self._audit_hook_installed = True
        self._call_monitor.start()
        self._active = True

    def stop(self) -> None:
        self._active = False
        self._call_monitor.stop()

    def _audit(self, event: str, arguments: tuple[object, ...]) -> None:
        if not self._active:
            return
        target = audit_read_target(event, arguments)
        if target is not None:
            self._notify(target)

    def _monitor_call(
        self,
        code: CodeType,
        instruction_offset: int,
        callable_object: object,
        arg0: object,
    ) -> None:
        del code, instruction_offset
        if self._active and call_reads_stdin(callable_object, arg0):
            self._notify("stdin")

    def _notify(self, target: str) -> None:
        current = inspect.currentframe()
        try:
            self._observer(target, current.f_back if current else None)
        finally:
            del current


def audit_read_target(event: str, arguments: Sequence[object]) -> str | None:
    if event == "open":
        if not open_event_reads(arguments):
            return None
        return _event_target(arguments)
    if event in _DIRECTORY_READ_EVENTS:
        return _event_target(arguments)
    if event == "builtins.input":
        return "stdin"
    return None


def call_reads_stdin(callable_object: object, arg0: object) -> bool:
    if callable_object is os.read:
        return arg0 == 0
    if callable_object is builtins.next:
        return _is_standard_input(arg0)

    name = getattr(callable_object, "__name__", None)
    if not isinstance(name, str) or name not in _STDIN_READ_METHODS:
        return False
    receiver = getattr(callable_object, "__self__", None)
    return _is_standard_input(receiver) or _is_standard_input(arg0)


def open_event_reads(arguments: Sequence[object]) -> bool:
    mode = arguments[1] if len(arguments) > 1 else None
    if isinstance(mode, str):
        return "r" in mode or "+" in mode

    flags = arguments[2] if len(arguments) > 2 else None
    if isinstance(flags, int):
        access_mode = flags & os.O_ACCMODE
        return access_mode in {os.O_RDONLY, os.O_RDWR}
    return False


def _event_target(arguments: Sequence[object]) -> str:
    return _display_target(arguments[0]) if arguments else "<unknown>"


def _display_target(value: object) -> str:
    if isinstance(value, (str, bytes)):
        return repr(value)
    if isinstance(value, int):
        return f"file descriptor {value}"
    return repr(value)


def _is_standard_input(candidate: object) -> bool:
    stdin = sys.stdin
    return candidate is stdin or candidate is getattr(stdin, "buffer", None)
