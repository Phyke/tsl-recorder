"""
R8 — post-save review prompt that auto-dismisses after 5 seconds.
"""

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from recorder.config import settings
from recorder.theme import button, label


def show_review_prompt(
    parent: QWidget,
    paths: Sequence[Path],
    *,
    on_review: Callable[[Path], None],
    timeout_ms: int = 5000,
    title: str = "Recording saved",
    review_label: str = "Review",
) -> QDialog | None:
    """
    Ask whether to review footage. Auto-destroys after timeout_ms.

    R8: message disappears in 5 seconds if the user ignores it.
    """
    existing = [Path(p) for p in paths if Path(p).is_file()]
    if not existing:
        return None

    bg = "#ffffff"
    ink = "#0f172a"
    muted = "#64748b"
    accent = "#0f766e"
    soft = "#e2e8f0"

    popup = QDialog(parent)
    popup.setWindowTitle(title)
    popup.setObjectName("reviewPopup")
    popup.setStyleSheet(f"QDialog#reviewPopup {{ background: {bg}; }}")
    popup.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on=True)
    popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, on=True)
    popup.setModal(False)

    names = "\n".join(f"• {p.name}" for p in existing[: settings.max_listed_files])
    if len(existing) > settings.max_listed_files:
        names += f"\n• …and {len(existing) - 6} more"

    column = QVBoxLayout(popup)
    column.setContentsMargins(20, 16, 20, 16)
    column.setSpacing(0)
    column.addWidget(
        label(
            popup,
            "Recording saved.\nReview footage now?\n(closes automatically in 5 seconds)",
            fg=ink,
            font_spec=("Segoe UI", 11),
        )
    )
    column.addSpacing(8)
    column.addWidget(label(popup, names, fg=muted, font_spec=("Segoe UI", 9)))
    column.addSpacing(10)

    row = QHBoxLayout()
    row.setSpacing(8)
    column.addLayout(row)

    closed = {"done": False}

    def review_first() -> None:
        dismiss()
        on_review(existing[0])

    def dismiss() -> None:
        if closed["done"]:
            return
        closed["done"] = True
        popup.close()

    row.addWidget(
        button(popup, review_label, review_first, color=accent, fg="#ffffff", font=("Segoe UI Semibold", 9), padx=14, pady=6)
    )
    row.addWidget(button(popup, "Dismiss", dismiss, color=soft, fg=ink, font=("Segoe UI", 9), padx=14, pady=6))
    row.addStretch(1)

    QTimer.singleShot(timeout_ms, popup, dismiss)
    popup.adjustSize()
    popup.setFixedSize(popup.sizeHint())
    popup.show()
    with contextlib.suppress(RuntimeError):
        popup.move(parent.mapToGlobal(QPoint(0, 0)) + QPoint(40, 40))
    return popup
