"""Run a function on the Qt UI thread from any thread (the PySide6 form of root.after(0, fn))."""

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal, Slot


class _Dispatcher(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        fn()


@dataclass
class _BridgeState:
    dispatcher: _Dispatcher | None = None


_state = _BridgeState()


# Call once on the UI thread, after QApplication exists.
def install() -> None:
    if _state.dispatcher is None:
        _state.dispatcher = _Dispatcher()


def call_in_ui(fn: Callable[[], None]) -> None:
    if _state.dispatcher is None:
        msg = "ui_bridge.install() was not called"
        raise RuntimeError(msg)
    _state.dispatcher.call.emit(fn)
