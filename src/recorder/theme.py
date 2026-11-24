"""Shared visual language for the unified POC1 app."""

import itertools
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPalette, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from recorder.config import settings

# Calm light shell — readable, not a debug console.
BG = "#eef2f6"
PANEL = "#ffffff"
SURFACE = "#f8fafc"
INK = "#0f172a"
MUTED = "#64748b"
BORDER = "#d0d7e2"
ACCENT = "#0f766e"
ACCENT_SOFT = "#ccfbf1"
BLUE = "#1d4ed8"
RED = "#b91c1c"
GREEN = "#15803d"
AMBER = "#b45309"
FIELD = "#ffffff"
# The Windows gray text color, which a disabled Tk Checkbutton uses.
DISABLED_TEXT = "#6d6d6d"


# The size of the base text in points, as the widgets use it now (poc1 uses Segoe UI 9, settings.base_font_size).
# The Settings tab changes it, and each font follows in proportion.
@dataclass
class _ThemeState:
    font_size: int


_state = _ThemeState(font_size=settings.font_size)

_names = itertools.count(1)


def apply_styles(app: QApplication) -> None:
    # Tk ignores the Windows dark mode, so the Qt palette stays light too.
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    app.setFont(font(("Segoe UI", settings.base_font_size)))
    # A message box uses the base text size, so it follows the Interface font size.
    message_font = f'font-family: "Segoe UI"; font-size: {_state.font_size}pt;'
    tab_size = 10 * _state.font_size / settings.base_font_size
    app.setStyleSheet(
        f"""
        QComboBox {{ background: {FIELD}; color: {INK}; border: 1px solid {BORDER}; padding: 3px 6px; }}
        QComboBox:disabled {{ background: #e2e8f0; color: #94a3b8; }}
        QComboBox QAbstractItemView {{ background: {FIELD}; color: {INK};
            selection-background-color: {ACCENT}; selection-color: #ffffff; }}
        QLineEdit {{ background: {FIELD}; color: {INK}; border: 1px solid {INK}; padding: 3px; }}
        QLineEdit:disabled {{ background: #e2e8f0; color: #94a3b8; }}
        QTabWidget::pane {{ border: 0; background: {BG}; }}
        QTabBar::tab {{ background: {SURFACE}; color: {MUTED}; padding: 10px 18px;
            font-family: "Segoe UI Semibold"; font-size: {tab_size:g}pt; }}
        QTabBar::tab:selected {{ background: {PANEL}; color: {INK}; }}
        QGroupBox {{ color: {INK}; }}
        QMessageBox QLabel, QMessageBox QPushButton {{ {message_font} }}
        """
    )


def _sized_font(family: str, size: int) -> QFont:
    result = QFont(family)
    result.setPointSizeF(size * _state.font_size / settings.base_font_size)
    return result


def font(spec: tuple) -> QFont:
    return _sized_font(str(spec[0]), int(spec[1]))


def font_size() -> int:
    return _state.font_size


# A new font size applies at once: the app font and the styles, each widget with a font of its own, and each text size.
def set_font_size(app: QApplication, size: int) -> None:
    old_scale = _state.font_size / settings.base_font_size
    widgets = app.allWidgets()
    own_font = [w for w in widgets if w.testAttribute(Qt.WidgetAttribute.WA_SetFont) and w.font().pointSizeF() > 0]
    # Each size at the base font size, kept before any font changes, because Qt copies some fonts (a combo box to its list) on a change.
    for widget in own_font:
        if widget.property("unscaled_point_size") is None:
            widget.setProperty("unscaled_point_size", widget.font().pointSizeF() / old_scale)
    _state.font_size = size
    for widget in own_font:
        widget_font = widget.font()
        widget_font.setPointSizeF(widget.property("unscaled_point_size") * size / settings.base_font_size)
        widget.setFont(widget_font)
    # The styles come last: they copy some parent fonts into Qt parts (the inner area of a list), so the parent fonts must be new first.
    apply_styles(app)
    for widget in widgets:
        if widget.property("text_size") is not None:
            _apply_text_size(widget)


# A size in characters of the widget's own font: the width of "0" times chars, plus pad pixels, and lines times the line height.
# A font size change measures it again.
def text_size(widget: QWidget, *, chars: int = 0, pad: int = 0, fixed: bool = True, lines: int = 0) -> None:
    widget.setProperty("text_size", [chars, pad, fixed, lines])
    _apply_text_size(widget)


def _apply_text_size(widget: QWidget) -> None:
    chars, pad, fixed, lines = widget.property("text_size")
    metrics = widget.fontMetrics()
    if chars and fixed:
        widget.setFixedWidth(metrics.horizontalAdvance("0") * chars + pad)
    elif chars:
        widget.setMinimumWidth(metrics.horizontalAdvance("0") * chars + pad)
    if lines:
        widget.setMinimumHeight(metrics.lineSpacing() * lines)


# One frame with a scoped stylesheet, so the background does not leak into child widgets.
def frame(
    parent: QWidget | None,
    bg: str,
    *,
    border: bool = False,
    padx: int = 0,
    pady: int = 0,
    kind: str = "v",
) -> tuple[QFrame, QLayout]:
    box = QFrame(parent)
    name = f"frame{next(_names)}"
    box.setObjectName(name)
    edge = f"border: 1px solid {BORDER};" if border else "border: 0;"
    box.setStyleSheet(f"QFrame#{name} {{ background: {bg}; {edge} }}")
    layout: QLayout
    if kind == "h":
        layout = QHBoxLayout(box)
    elif kind == "grid":
        layout = QGridLayout(box)
    else:
        layout = QVBoxLayout(box)
    layout.setContentsMargins(padx, pady, padx, pady)
    layout.setSpacing(0)
    return box, layout


def label(
    parent: QWidget | None,
    text: str = "",
    *,
    fg: str = INK,
    bg: str | None = None,
    font_spec: tuple = ("Segoe UI", 9),
    padx: int = 0,
    pady: int = 0,
    wrap: bool = False,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
) -> QLabel:
    widget = QLabel(text, parent)
    widget.setFont(font(font_spec))
    set_label_colors(widget, fg, bg, padx=padx, pady=pady)
    widget.setWordWrap(wrap)
    widget.setAlignment(align)
    # A Tk label is cut off when its cell is too narrow. It never makes the window wider.
    if not wrap:
        widget.setMinimumWidth(1)
    return widget


def set_label_colors(widget: QLabel, fg: str, bg: str | None = None, *, padx: int = 0, pady: int = 0) -> None:
    background = f"background: {bg};" if bg else "background: transparent;"
    widget.setStyleSheet(f"QLabel {{ color: {fg}; {background} padding: {pady}px {padx}px; border: 0; }}")


# A Tk Checkbutton look: a white square, filled with ACCENT_SOFT (the Tk selectcolor) when checked, and gray text when disabled.
def style_check_box(box: QCheckBox) -> None:
    box.setStyleSheet(f"QCheckBox {{ color: {INK}; }} QCheckBox:disabled {{ color: {DISABLED_TEXT}; }}")

    def fill(*, checked: bool) -> None:
        palette = box.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(ACCENT_SOFT if checked else FIELD))
        box.setPalette(palette)

    box.toggled.connect(lambda checked: fill(checked=checked))
    fill(checked=box.isChecked())


def button(
    master: QWidget | None,
    text: str,
    command: Callable[[], None],
    *,
    color: str = ACCENT,
    fg: str = "#ffffff",
    font: tuple = ("Segoe UI Semibold", 9),
    padx: int = 14,
    pady: int = 8,
    state: str = "normal",
) -> QPushButton:
    btn = QPushButton(text, master)
    btn.setFont(_sized_font(str(font[0]), int(font[1])))
    btn.setStyleSheet(
        f"QPushButton {{ background: {color}; color: {fg}; border: 0; padding: {pady}px {padx}px; }}"
        f"QPushButton:disabled {{ color: #94a3b8; }}"
    )
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.clicked.connect(lambda _checked=False: command())
    btn.setEnabled(state != "disabled")
    return btn


# A combo box that leaves the mouse wheel to the page (a ttk Combobox does not change its value on the wheel).
# Its size comes from 20 characters (the ttk Combobox default width), not from the longest item.
# The combo box and its list use the base text size, so they follow the Interface font size like the text.
class NoWheelComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(font(("Segoe UI", settings.base_font_size)))
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(20)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()

    # The list takes the font of the combo box each time it opens. A list can lose its own font when a window
    # above it gets a parent later, and then it shows the Windows list font.
    def showPopup(self) -> None:  # noqa: N802
        self.view().setFont(self.font())
        super().showPopup()


# The text that the combo box shows: the selected item, or the placeholder (like a ttk Combobox textvariable).
def combo_text(combo: QComboBox) -> str:
    if combo.currentIndex() >= 0:
        return combo.currentText()
    return combo.placeholderText()


def set_combo_text(combo: QComboBox, text: str) -> None:
    index = combo.findText(text)
    if index >= 0:
        combo.setCurrentIndex(index)
        return
    combo.setPlaceholderText(text)
    combo.setCurrentIndex(-1)


# Replace the items and keep the shown text, as setting ttk Combobox values does.
def set_combo_values(combo: QComboBox, values: list[str]) -> None:
    shown = combo_text(combo)
    combo.clear()
    combo.addItems(values)
    set_combo_text(combo, shown)


# An image label that does not grow with its picture (a Tk label in a fixed cell).
def image_label(parent: QWidget | None, text: str, *, bg: str = "#0b1220", fg: str = "#94a3b8", size: int = 10) -> QLabel:
    widget = label(parent, text, fg=fg, bg=bg, font_spec=("Segoe UI", size), align=Qt.AlignmentFlag.AlignCenter)
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    widget.setMinimumSize(1, 1)
    return widget


def show_rgb(widget: QLabel, rgb: np.ndarray) -> None:
    rgb = np.ascontiguousarray(rgb)
    height, width = rgb.shape[:2]
    image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888)
    widget.setPixmap(QPixmap.fromImage(image))


def show_text(widget: QLabel, text: str) -> None:
    widget.clear()
    widget.setText(text)
