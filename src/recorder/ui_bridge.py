"""Run a function on the Qt UI thread from any thread (the PySide6 form of root.after(0, fn))."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot


class _Dispatcher(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        fn()


_dispatcher: _Dispatcher | None = None


# Call once on the UI thread, after QApplication exists.
def install() -> None:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _Dispatcher()


def call_in_ui(fn: Callable[[], None]) -> None:
    if _dispatcher is None:
        raise RuntimeError("ui_bridge.install() was not called")
    _dispatcher.call.emit(fn)
