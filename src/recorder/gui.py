"""
Unified POC1 app — Setup / Record / Library in one window.

Launch:
  uv run recorder
  uv run python -m recorder
"""

import argparse
import contextlib
import json
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
from pydantic import ValidationError
from PySide6.QtCore import QEvent, QLocale, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from recorder import ui_bridge
from recorder.bag_recorder import BUILD_ID
from recorder.cards import CameraCard
from recorder.config import load_settings, save_settings, settings
from recorder.devices import (
    PreviewOpenCancelledError,
    clear_preview_cancel,
    is_fhd_high_rate,
    request_preview_cancel,
    too_many_1080p120,
)
from recorder.library import LibraryPage
from recorder.preview_draw import bgr_to_rgb_fill, hud_lines_for_source, overlay_hud
from recorder.quiet import configure_app_logging, silence_opencv
from recorder.review import show_review_prompt
from recorder.session import CameraSlot, MultiCamSession
from recorder.theme import (
    ACCENT,
    ACCENT_SOFT,
    BG,
    BLUE,
    BORDER,
    GREEN,
    INK,
    MUTED,
    PANEL,
    RED,
    SURFACE,
    NoWheelComboBox,
    button,
    font,
    font_size,
    frame,
    image_label,
    label,
    set_font_size,
    show_rgb,
    show_text,
    text_size,
)
from recorder.ui_bridge import call_in_ui
from recorder.user_messages import (
    bag_file_missing,
    capture_not_120_status,
    no_frames_captured_message,
    preview_failed_message,
)
from recorder.win_names import ffmpeg_available

configure_app_logging()
logger = logging.getLogger("poc1.app")


def _ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(
        parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    return answer == QMessageBox.StandardButton.Yes


# A widget inside a container with outer margins (the padx / pady of a Tk pack).
def _margin(widget: QWidget, left: int, top: int, right: int, bottom: int) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(left, top, right, bottom)
    layout.setSpacing(0)
    layout.addWidget(widget)
    return box


class AppWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.close_handler: Callable[[], bool] | None = None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.close_handler is not None and not self.close_handler():
            event.ignore()
            return
        event.accept()


# The mouse wheel scrolls the Setup cards from anywhere on the Setup page (Tk bind_all("<MouseWheel>")).
class _WheelFilter(QObject):
    def __init__(self, app: UnifiedApp) -> None:
        super().__init__()
        self.app = app

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        return self.app.on_mousewheel(watched, event)


class UnifiedApp:
    """One comfortable shell over the existing multi-cam + library pipelines."""

    def __init__(self, root: AppWindow, n_slots: int = 2, out_dir: Path | None = None, settings_path: Path | None = None) -> None:
        self.root = root
        # With no settings_path (a test run), no change is saved. main() loads the file into settings before this.
        self.settings_path = settings_path
        self.session = MultiCamSession(
            n_slots=n_slots,
            out_dir=out_dir or settings.default_save_folder,
        )
        self.session.station = settings.station
        self.cards: list[CameraCard] = []
        self.busy = False
        self._stopping = False
        self.last_reports: dict[str, dict] = {}
        self._closing = False
        self._record_tiles: list[QLabel] = []

        root.setWindowTitle(f"POC1 Recorder — {BUILD_ID}")
        root.resize(1240, 860)
        root.setMinimumSize(980, 700)
        root.close_handler = self._on_close
        set_font_size(QApplication.instance(), settings.font_size)

        central = QWidget(root)
        central.setObjectName("central")
        central.setStyleSheet(f"QWidget#central {{ background: {BG}; }}")
        root.setCentralWidget(central)
        self._shell = QVBoxLayout(central)
        self._shell.setContentsMargins(0, 0, 0, 0)
        self._shell.setSpacing(0)

        self._build_chrome()
        self._build_pages()
        self.rebuild_cards(auto_assign=False)
        self.refresh_cameras(initial=True)
        self.library.sync_folder(self.session.out_dir)
        self.refresh_record_gate()
        self._wheel_filter = _WheelFilter(self)
        QApplication.instance().installEventFilter(self._wheel_filter)
        self._tick()

    # ── chrome ───────────────────────────────────────────────────────────

    def _build_chrome(self) -> None:
        header, row = frame(None, PANEL, border=True, padx=20, pady=14, kind="h")
        self._shell.addWidget(header)
        row.addWidget(label(header, "POC1 Recorder", fg=INK, font_spec=("Segoe UI Semibold", 18)))
        row.addSpacing(8)
        row.addWidget(label(header, f"  {BUILD_ID}", fg=MUTED, font_spec=("Segoe UI", 9)))
        row.addSpacing(16)
        row.addWidget(label(header, "Cameras  ·  Record  ·  Library", fg=MUTED, font_spec=("Segoe UI", 10)))
        row.addStretch(1)
        self.count_label = label(
            header, "", fg=ACCENT, bg=ACCENT_SOFT, font_spec=("Segoe UI Semibold", 9), padx=10, pady=4
        )
        # pack(side="right") with no fill: the badge keeps its own height, in the middle of the header.
        row.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.notebook = QTabWidget()
        self._shell.addWidget(_margin(self.notebook, 12, 10, 12, 10), 1)

        self.status_bar, status_layout = frame(None, SURFACE, padx=16, pady=8)
        self.status_label = label(
            self.status_bar,
            "Start on Setup: refresh cameras, start previews, then Record.",
            fg=INK,
            font_spec=("Segoe UI", 9),
        )
        status_layout.addWidget(self.status_label)
        self._shell.addWidget(self.status_bar)

    def _build_pages(self) -> None:
        self.setup_page = QWidget()
        self.record_page = QWidget()
        self.library_page = QWidget()
        self.settings_page = QWidget()
        for page in (self.setup_page, self.record_page, self.library_page, self.settings_page):
            page.setObjectName("page")
            page.setStyleSheet(f"QWidget#page {{ background: {BG}; }}")
        self.notebook.addTab(self.setup_page, "  Setup  ")
        self.notebook.addTab(self.record_page, "  Record  ")
        self.notebook.addTab(self.library_page, "  Library  ")
        self.notebook.addTab(self.settings_page, "  Settings  ")

        self._build_setup()
        self._build_record()
        self.library = LibraryPage(self.library_page, self)
        library_layout = QVBoxLayout(self.library_page)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.addWidget(self.library)
        self._build_settings()
        self.notebook.currentChanged.connect(lambda _index: self._on_tab())

    # The Settings tab: a list of setting groups on the left, and the page of the selected group on the right.
    def _build_settings(self) -> None:
        row = QHBoxLayout(self.settings_page)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(8)
        self.settings_list = QListWidget()
        self.settings_list.setFont(font(("Segoe UI", 10)))
        text_size(self.settings_list, chars=25, pad=5)
        self.settings_list.setStyleSheet(
            f"QListWidget {{ background: {PANEL}; border: 1px solid {BORDER}; outline: 0; }}"
            f"QListWidget::item {{ color: {INK}; padding: 8px 12px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}"
        )
        self.settings_stack = QStackedWidget()
        for name, rows in (("General", self._general_rows()), ("Interface", self._interface_rows())):
            self.settings_list.addItem(name)
            page, page_layout = frame(None, PANEL, border=True, padx=16, pady=12)
            page_layout.addWidget(label(page, name, fg=INK, font_spec=("Segoe UI Semibold", 12)))
            page_layout.addSpacing(12)
            # One row for each setting: the name, the list of choices, and a note.
            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            for index, (text, combo, note) in enumerate(rows):
                grid.addWidget(label(page, text, fg=INK), index, 0)
                grid.addWidget(combo, index, 1)
                grid.addWidget(label(page, note, fg=MUTED), index, 2)
            grid.setColumnStretch(3, 1)
            page_layout.addLayout(grid)
            page_layout.addStretch(1)
            self.settings_stack.addWidget(page)
        self.settings_list.currentRowChanged.connect(self.settings_stack.setCurrentIndex)
        self.settings_list.setCurrentRow(0)
        row.addWidget(self.settings_list)
        row.addWidget(self.settings_stack, 1)

    def _general_rows(self) -> list[tuple[str, NoWheelComboBox, str]]:
        self.station_combo = NoWheelComboBox()
        self.station_combo.setMinimumContentsLength(10)
        self.station_combo.addItem("Not set", "")
        for station in settings.stations:
            self.station_combo.addItem(station, station)
        self.station_combo.setCurrentIndex(self.station_combo.findData(settings.station))
        self.station_combo.activated.connect(lambda index: self._set_station(self.station_combo.itemData(index)))
        self.slots_combo = NoWheelComboBox()
        self.slots_combo.setMinimumContentsLength(4)
        for count in range(settings.camera_slot_range[0], settings.camera_slot_range[1] + 1):
            self.slots_combo.addItem(str(count), count)
        self.slots_combo.setCurrentIndex(self.slots_combo.findData(settings.camera_slots))
        self.slots_combo.activated.connect(lambda index: self._save_setting("camera_slots", self.slots_combo.itemData(index)))
        return [
            ("Station", self.station_combo, "Written into each recording report."),
            ("Camera slots at start", self.slots_combo, "Applies at the next start."),
        ]

    # The font size of the whole app, in points of the base text. A size picked from the list applies at once.
    # A list and not a spin box: a font change moves the whole page, so spin box arrows move away from the mouse after each click.
    def _interface_rows(self) -> list[tuple[str, NoWheelComboBox, str]]:
        self.font_size_combo = NoWheelComboBox()
        self.font_size_combo.setMinimumContentsLength(6)
        for size in range(settings.font_size_range[0], settings.font_size_range[1] + 1):
            self.font_size_combo.addItem(f"{size} pt", size)
        self.font_size_combo.setCurrentIndex(self.font_size_combo.findData(font_size()))
        self.font_size_combo.activated.connect(lambda index: self._set_font_size(self.font_size_combo.itemData(index)))
        return [("Font size", self.font_size_combo, "")]

    def _set_station(self, station: str) -> None:
        self.session.station = station
        self._save_setting("station", station)

    def _set_font_size(self, size: int) -> None:
        set_font_size(QApplication.instance(), size)
        self._save_setting("font_size", size)

    def _save_setting(self, key: str, value: int | str) -> None:
        setattr(settings, key, value)
        if self.settings_path is not None:
            save_settings(self.settings_path)

    # The Save folder of the Setup tab is saved, so the next start opens with it.
    def _remember_save_folder(self) -> None:
        folder = self.out_entry.text().strip()
        if not folder:
            return
        folder = str(Path(folder).expanduser().resolve())
        if folder != settings.save_folder:
            self._save_setting("save_folder", folder)

    def _build_setup(self) -> None:
        page = QVBoxLayout(self.setup_page)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        tip, tip_layout = frame(None, ACCENT_SOFT, padx=16, pady=10)
        tip_layout.addWidget(
            label(
                tip,
                "1) Refresh cameras  ·  2) Pick device & configuration  ·  "
                "3) Check Armed on cameras to record  ·  "
                "4) Start preview until live  ·  5) Go to Record",
                fg=ACCENT,
                font_spec=("Segoe UI", 9),
            )
        )
        page.addWidget(_margin(tip, 4, 4, 4, 0))

        folder, folder_row = frame(None, BG, padx=8, pady=10, kind="h")
        page.addWidget(folder)
        folder_row.addWidget(label(folder, "Save folder", fg=MUTED, font_spec=("Segoe UI Semibold", 9)))
        folder_row.addSpacing(10)
        self.out_entry = QLineEdit(str(self.session.out_dir.resolve()), folder)
        self.out_entry.setFont(font(("Segoe UI", 9)))
        self.out_entry.editingFinished.connect(self._remember_save_folder)
        folder_row.addWidget(self.out_entry, 1)
        folder_row.addSpacing(10)
        folder_row.addWidget(button(folder, "Browse…", self.browse_folder, color="#475569"))

        toolbar, toolbar_row = frame(None, BG, padx=8, pady=4, kind="h")
        page.addWidget(toolbar)
        self.refresh_btn = button(toolbar, "Refresh cameras", self.refresh_cameras, color="#475569")
        toolbar_row.addWidget(self.refresh_btn)
        toolbar_row.addSpacing(6)
        self.add_btn = button(toolbar, "Add camera", self.add_camera, color=ACCENT)
        toolbar_row.addWidget(self.add_btn)
        toolbar_row.addSpacing(6)
        self.start_all_btn = button(toolbar, "Start all previews", self.start_all_previews, color=BLUE)
        toolbar_row.addWidget(self.start_all_btn)
        toolbar_row.addSpacing(6)
        self.stop_all_btn = button(toolbar, "Stop all previews", self.stop_all_previews, color="#475569")
        toolbar_row.addWidget(self.stop_all_btn)
        toolbar_row.addStretch(1)

        adv = QGroupBox(" Advanced ")
        adv.setFont(font(("Segoe UI", 9)))
        adv_layout = QVBoxLayout(adv)
        adv_layout.setContentsMargins(8, 8, 8, 8)
        adv_layout.addWidget(
            label(
                adv,
                "MP4 + optional RealSense .db3/.bag stay in the save folder. "
                "JSON reports and sysmon CSV go in a meta/ subfolder. "
                "Close RealSense Viewer before Record so Elgato is not locked. "
                "Library can export bags to MP4.",
                fg=MUTED,
                font_spec=("Segoe UI", 8),
                wrap=True,
            )
        )
        page.addWidget(_margin(adv, 8, 4, 8, 0))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {BG}; }}")
        holder = QWidget()
        holder.setObjectName("cardsHolder")
        holder.setStyleSheet(f"QWidget#cardsHolder {{ background: {BG}; }}")
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_frame = QWidget(holder)
        self.cards_grid = QGridLayout(self.cards_frame)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(12)
        holder_layout.addWidget(self.cards_frame)
        holder_layout.addStretch(1)
        self.scroll.setWidget(holder)
        page.addWidget(_margin(self.scroll, 4, 8, 4, 8), 1)

    def _build_record(self) -> None:
        page = QVBoxLayout(self.record_page)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        tip, tip_layout = frame(None, SURFACE, padx=16, pady=10)
        tip_layout.addWidget(
            label(
                tip,
                "Multi-cam: RealSense/webcam @30 (or @60 if the device has it) + Elgato 1080p120 together. "
                "Do not arm two 1080p@120 cameras. HDMI must be 1080p120 for true Elgato 120.",
                fg=INK,
                font_spec=("Segoe UI Semibold", 9),
                wrap=True,
            )
        )
        self.record_tip_label = label(tip, "", fg=MUTED, font_spec=("Segoe UI", 9), wrap=True)
        tip_layout.addWidget(self.record_tip_label)
        page.addWidget(_margin(tip, 4, 4, 4, 0))

        actions, actions_row = frame(None, BG, padx=8, pady=12, kind="h")
        page.addWidget(actions)
        self.record_btn = button(
            actions,
            "●  Record",
            self.start_recording,
            color=RED,
            font=("Segoe UI Semibold", 12),
            padx=28,
            pady=12,
        )
        actions_row.addWidget(self.record_btn)
        actions_row.addSpacing(10)
        self.stop_record_btn = button(
            actions,
            "■  Stop",
            self.stop_recording,
            color=GREEN,
            font=("Segoe UI Semibold", 12),
            padx=28,
            pady=12,
            state="disabled",
        )
        actions_row.addWidget(self.stop_record_btn)
        actions_row.addStretch(1)
        actions_row.addWidget(button(actions, "Last report", self.show_report, color="#475569"))
        actions_row.addSpacing(6)
        actions_row.addWidget(button(actions, "Open save folder", self.open_output_folder, color="#475569"))

        self.record_grid = QWidget()
        self.record_grid_layout = QGridLayout(self.record_grid)
        self.record_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.record_grid_layout.setSpacing(12)
        page.addWidget(_margin(self.record_grid, 8, 8, 8, 8), 1)

    # ── navigation helpers ───────────────────────────────────────────────

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_record_tip(self, text: str) -> None:
        self.record_tip_label.setText(text)

    def show_library(self, path: Path | None = None) -> None:
        self.notebook.setCurrentWidget(self.library_page)
        self.library.sync_folder(Path(self.out_entry.text()))
        if path is not None:
            self.library.select_path(Path(path))

    def _on_tab(self) -> None:
        current = self.notebook.currentWidget()
        if current is self.library_page:
            self.library.sync_folder(Path(self.out_entry.text()))
        elif current is self.record_page:
            self.refresh_record_gate()
            self._rebuild_record_tiles()

    def on_mousewheel(self, watched: QWidget, event: QWheelEvent) -> bool:
        if self._closing or watched.window() is not self.root:
            return False
        if self.notebook.currentWidget() is not self.setup_page:
            return False
        if watched is self.scroll or self.scroll.isAncestorOf(watched):
            return False
        bar = self.scroll.verticalScrollBar()
        steps = int(-1 * (event.angleDelta().y() / 120))
        bar.setValue(bar.value() + steps * bar.singleStep() * 3)
        return True

    # ── setup actions ────────────────────────────────────────────────────

    def rebuild_cards(self, *, auto_assign: bool = False) -> None:
        for card in self.cards:
            self.cards_grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        for col in range(2):
            self.cards_grid.setColumnStretch(col, 1)
        for index, slot in enumerate(self.session.slots):
            card = CameraCard(self.cards_frame, self, slot)
            self.cards_grid.addWidget(card, index // 2, index % 2)
            self.cards.append(card)
        for card in self.cards:
            card.load_devices(auto_assign=auto_assign)
        self.count_label.setText(f"{len(self.session.slots)} cameras")
        self._rebuild_record_tiles()
        self.refresh_record_gate()

    def add_camera(self) -> None:
        if self.busy:
            return
        try:
            self.session.add_slot()
            self.rebuild_cards(auto_assign=False)
            self.set_status(f"Camera {len(self.session.slots)} added.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.root, "Add camera", str(exc))

    def remove_camera(self, slot_id: int) -> None:
        if self.busy:
            return
        if not _ask_yes_no(self.root, "Remove camera", f"Remove Camera {slot_id + 1}?"):
            return
        try:
            self.session.remove_slot(slot_id)
            self.rebuild_cards()
            self.set_status("Camera removed.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.root, "Remove camera", str(exc))

    def refresh_cameras(self, *, initial: bool = False) -> None:
        if self.busy:
            return
        if self.session.previews_running:
            if not _ask_yes_no(self.root, "Refresh cameras", "This stops all previews. Continue?"):
                return
            self.session.stop_previews()
        self._set_busy(busy=True)
        self.set_status("Scanning cameras… (UI stays responsive)")

        def worker() -> None:
            devices: list = []
            error: str | None = None
            try:
                devices = self.session.refresh_devices(include_fake=True)
            except Exception as exc:
                logger.exception("camera refresh failed")
                error = str(exc)
            call_in_ui(
                lambda d=devices, err=error, init=initial: self._refresh_cameras_done(
                    d, err, initial=init
                )
            )

        threading.Thread(target=worker, name="app-refresh-cameras", daemon=True).start()

    def _refresh_cameras_done(
        self, devices: list, error: str | None, *, initial: bool
    ) -> None:
        self._set_busy(busy=False)
        if error:
            QMessageBox.critical(self.root, "Camera scan failed", error)
            return
        self.rebuild_cards(auto_assign=initial)
        real = [d for d in devices if d.kind != "fake"]
        rs_n = sum(1 for d in real if d.kind == "realsense")
        elg_n = sum(1 for d in real if d.device_tag == "elgato")
        uvc_n = sum(
            1
            for d in real
            if d.kind == "uvc" and d.device_tag not in {"elgato", "realsense-uvc"}
        )
        parts = []
        if rs_n:
            parts.append(f"RealSense x{rs_n}")
        if elg_n:
            parts.append(f"Elgato x{elg_n}")
        if uvc_n:
            parts.append(f"UVC x{uvc_n}")
        summary = ", ".join(parts) or "none yet"
        names = ", ".join(d.name for d in real[:4]) or "none yet"
        status = (
            f"Found {len(real)} camera(s) ({summary}): {names}. "
            "Start preview on each camera you plan to record."
        )
        if elg_n and sys.platform == "win32":
            try:
                if not ffmpeg_available():
                    status += (
                        " Warning: ffmpeg missing — Elgato open may fail; "
                        "install ffmpeg on PATH, then Refresh."
                    )
            except Exception:
                logger.debug("Could not check for ffmpeg", exc_info=True)
        self.set_status(status)

    def _clear_opening_placeholders(self) -> None:
        for card in self.cards:
            slot = self.session.slots[card.slot_id]
            if slot.pipeline is not None:
                continue
            try:
                text = str(card.preview.text() or "")
            except Exception:
                logger.debug("Could not read the preview text of camera slot %s", card.slot_id, exc_info=True)
                continue
            if "Opening" in text:
                show_text(card.preview, "Start preview to see live video")

    def _live_preview_count(self) -> int:
        return sum(
            1
            for s in self.session.slots
            if s.pipeline is not None and s.get_preview_frame() is not None
        )

    def browse_folder(self) -> None:
        if self.busy:
            return
        chosen = QFileDialog.getExistingDirectory(self.root, "", self.out_entry.text())
        if chosen:
            self.out_entry.setText(chosen)
            self.session.set_out_dir(chosen)
            self.library.sync_folder(Path(chosen))
            self._remember_save_folder()

    def _sync_all(self) -> None:
        self.session.set_out_dir(self.out_entry.text())
        for card in self.cards:
            card.sync_controls()

    def start_all_previews(self) -> None:
        if self.busy:
            return
        self._sync_all()
        clear_preview_cancel()
        self._set_busy(busy=True)
        self.set_status("Opening cameras… (UI stays responsive)")
        for card in self.cards:
            if self.session.slots[card.slot_id].pipeline is None:
                show_text(card.preview, "Opening camera…")

        def worker() -> None:
            error: str | None = None
            cancelled = False
            try:
                self.session.start_previews()
            except PreviewOpenCancelledError:
                cancelled = True
            except Exception as exc:
                logger.exception("start_previews failed")
                error = str(exc)
            if cancelled:
                try:
                    self.session.stop_previews()
                except Exception:
                    logger.exception("stop after cancel failed")
            call_in_ui(
                lambda err=error, was_cancelled=cancelled: self._previews_started(
                    err, cancelled=was_cancelled
                )
            )

        threading.Thread(target=worker, name="app-preview-start", daemon=True).start()

    def start_slot_preview_async(self, slot_id: int) -> None:
        if self.busy:
            return
        try:
            self.cards[slot_id].sync_controls()
        except Exception:
            logger.debug("Could not sync the controls of camera slot %s", slot_id, exc_info=True)
        clear_preview_cancel()
        self._set_busy(busy=True)
        self.set_status(f"Opening camera {slot_id + 1}…")
        with contextlib.suppress(RuntimeError):
            show_text(self.cards[slot_id].preview, "Opening camera…")

        def worker() -> None:
            error: str | None = None
            cancelled = False
            try:
                self.session.start_slot_preview(slot_id)
            except PreviewOpenCancelledError:
                cancelled = True
            except Exception as exc:
                logger.exception("start_slot_preview failed")
                error = str(exc)
            if cancelled:
                try:
                    self.session.stop_slot_preview(slot_id)
                except Exception:
                    logger.exception("stop slot after cancel failed")
            call_in_ui(
                lambda err=error, sid=slot_id, was_cancelled=cancelled: self._slot_preview_started(
                    sid, err, cancelled=was_cancelled
                )
            )

        threading.Thread(
            target=worker, name=f"app-preview-slot{slot_id}", daemon=True
        ).start()

    def _previews_started(self, error: str | None, *, cancelled: bool = False) -> None:
        self._set_busy(busy=False)
        clear_preview_cancel()
        self._clear_opening_placeholders()
        if cancelled:
            for card in self.cards:
                if self.session.slots[card.slot_id].pipeline is None:
                    show_text(card.preview, "Preview stopped")
            self.set_status("Preview open cancelled.")
            self.refresh_record_gate()
            return
        for card in self.cards:
            try:
                card.sync_opened_mode_from_source()
            except Exception:
                logger.debug("Could not sync the opened mode of camera slot %s", card.slot_id, exc_info=True)
        live = self._live_preview_count()
        opened = len([s for s in self.session.slots if s.pipeline])
        waiting = opened - live
        if error:
            msg = f"{live} preview(s) showing video"
            if waiting > 0:
                msg += f" ({waiting} opened but no frames yet)"
            self.set_status(f"{msg}. Some failed.")
            QMessageBox.critical(
                self.root,
                "Preview",
                error + "\n\nClose Zoom/Teams/Camera, Elgato 4K Capture Utility, "
                "and extra POC1 windows. For Elgato: HDMI ON, install ffmpeg, "
                "Refresh, then try Camera 2 Start preview alone.",
            )
        else:
            msg = f"{live} preview(s) showing video"
            if waiting > 0:
                msg += f" ({waiting} opened but no frames yet — wait or restart preview)"
            self.set_status(f"{msg}. Open Record when ready.")
        self.refresh_record_gate()

    def _slot_preview_started(
        self, slot_id: int, error: str | None, *, cancelled: bool = False
    ) -> None:
        self._set_busy(busy=False)
        clear_preview_cancel()
        self._clear_opening_placeholders()
        if cancelled:
            with contextlib.suppress(RuntimeError):
                show_text(self.cards[slot_id].preview, "Preview stopped")
            self.set_status(f"Camera {slot_id + 1} open cancelled.")
            self.refresh_record_gate()
            return
        if error:
            self.set_status(f"Camera {slot_id + 1} preview failed.")
            slot = self.session.slots[slot_id]
            tag = getattr(slot.camera, "device_tag", "") if slot.camera else ""
            QMessageBox.critical(
                self.root,
                f"Camera {slot_id + 1}",
                preview_failed_message(error, device_tag=tag),
            )
        else:
            try:
                self.cards[slot_id].sync_opened_mode_from_source()
            except Exception:
                logger.debug("Could not sync the opened mode of camera slot %s", slot_id, exc_info=True)
            self.set_status(f"Camera {slot_id + 1} is live.")
            QTimer.singleShot(3500, self.root, lambda: self._warn_slot_if_no_frames(slot_id))
        self.refresh_record_gate()

    def _warn_slot_if_no_frames(self, slot_id: int) -> None:
        if slot_id >= len(self.session.slots):
            return
        slot = self.session.slots[slot_id]
        if slot.pipeline is None or slot.get_preview_frame() is not None:
            return
        cam = slot.camera
        if cam is not None and cam.kind == "realsense":
            QMessageBox.warning(
                self.root,
                f"Camera {slot_id + 1}",
                "RealSense opened but no image arrived.\n\n"
                "Close Intel RealSense Viewer, use USB 3, pick 1280x720@30 bgr8, "
                "then Start preview again.",
            )
            return
        if cam is not None and cam.device_tag == "elgato":
            QMessageBox.warning(
                self.root,
                f"Camera {slot_id + 1}",
                "Elgato opened but no image yet.\n\n"
                "Use 1920x1080@120 mjpg (not bgr8), confirm HDMI is on, "
                "close Elgato 4K Capture Utility, then Start preview again.",
            )
            return
        QMessageBox.warning(
            self.root,
            f"Camera {slot_id + 1}",
            "Opened, but no image yet. Close other camera apps, check the webcam, "
            "then Start preview again.",
        )

    def stop_all_previews(self) -> None:
        if self.session.is_recording:
            QMessageBox.warning(self.root, "Recording", "Stop recording first.")
            return
        if self.busy:
            request_preview_cancel()
            self.set_status("Stopping camera open…")
            return
        self.session.stop_previews()
        for card in self.cards:
            show_text(card.preview, "Preview stopped")
        self.set_status("All previews stopped.")
        self.refresh_record_gate()

    # ── record gate + tiles ──────────────────────────────────────────────

    def _armed_live_ready(self) -> tuple[bool, str]:
        armed = [s for s in self.session.slots if s.armed]
        if not armed:
            return False, "Arm at least one camera on Setup (Include when recording)."
        waiting = []
        for s in armed:
            if s.pipeline is None:
                waiting.append(f"Camera {s.slot_id + 1} (start preview)")
                continue
            if s.get_preview_frame() is None:
                waiting.append(f"Camera {s.slot_id + 1} (wait for live picture)")
        if waiting:
            return False, "Need live preview before Record: " + "; ".join(waiting)
        return True, f"Ready to record {len(armed)} camera(s)."

    def _elgato_live_lines(self) -> list[str]:
        lines: list[str] = []
        for slot in self.session.slots:
            if not slot.armed or slot.pipeline is None:
                continue
            src = slot.pipeline.source
            if getattr(src, "device_tag", "") != "elgato":
                continue
            measured = float(getattr(src, "actual_fps", 0) or 0)
            requested = int(
                getattr(src, "requested_fps", 0)
                or getattr(src, "target_fps", 0)
                or 0
            )
            stamp = int(getattr(src, "target_fps", 0) or 0)
            if measured < 1:
                continue
            line = (
                f"{slot.prefix}: requested {requested} fps, delivering ~{measured:.0f}"
            )
            if requested >= settings.high_rate_fps and measured < requested * 0.85:
                line += (
                    f" ({capture_not_120_status(requested, measured, stamp)})"
                )
            lines.append(line)
        return lines

    def _prepare_high_rate_solo(self) -> bool:
        """Refuse two 1080p@≥90 takes. Allow one 120 + @30/@60 companions."""
        armed = [s for s in self.session.slots if s.armed]
        if too_many_1080p120([s.mode for s in armed]):
            high = [s.prefix for s in armed if is_fhd_high_rate(s.mode)]
            QMessageBox.critical(
                self.root,
                "Too many 1080p@120 cameras",
                "Only one 1920x1080@90+ camera per Record.\n"
                f"Currently: {', '.join(high)}.\n\n"
                "Keep Elgato (or fake) 1080p120, and set the others to @30 or @60. "
                "Webcam + fake 1080p120 together drops frames.",
            )
            return False
        return True

    def refresh_record_gate(self) -> None:
        if self.busy or self.session.is_recording:
            return
        ok, tip = self._armed_live_ready()
        self._set_record_tip(tip)
        self.record_btn.setEnabled(ok)

    def _rebuild_record_tiles(self) -> None:
        while self.record_grid_layout.count():
            item = self.record_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._record_tiles = []
        slots = self.session.slots
        cols = 2 if len(slots) > 1 else 1
        for col in range(cols):
            self.record_grid_layout.setColumnStretch(col, 1)
        for index, slot in enumerate(slots):
            cell, cell_layout = frame(self.record_grid, PANEL, border=True)
            cell_layout.setContentsMargins(1, 1, 1, 1)
            self.record_grid_layout.addWidget(cell, index // cols, index % cols)
            self.record_grid_layout.setRowStretch(index // cols, 1)
            title = label(
                cell,
                f"Camera {slot.slot_id + 1}  ·  {slot.prefix}"
                + ("  ·  armed" if slot.armed else "  ·  not armed"),
                fg=INK,
                font_spec=("Segoe UI Semibold", 10),
                padx=10,
                pady=6,
            )
            cell_layout.addWidget(title)
            tile = image_label(cell, "No preview")
            cell_layout.addWidget(_margin(tile, 4, 0, 4, 4), 1)
            self._record_tiles.append(tile)

    def _tick_record_tiles(self) -> None:
        if len(self._record_tiles) != len(self.session.slots):
            return
        for slot, tile in zip(self.session.slots, self._record_tiles, strict=False):
            frame_bgr = slot.get_preview_frame()
            if frame_bgr is None:
                continue
            tw = tile.width()
            th = tile.height()
            if tw < settings.min_tile_px or th < settings.min_tile_px:
                continue
            src = slot.pipeline.source if slot.pipeline else None
            depth = slot.get_depth_preview_frame()
            try:
                if depth is not None and getattr(src, "device_tag", "") == "realsense":
                    half = max(tw // 2, 40)
                    left = bgr_to_rgb_fill(frame_bgr, half, th)
                    right = bgr_to_rgb_fill(depth, max(tw - half, 40), th)
                    if left.shape[0] != right.shape[0] or left.ndim != right.ndim:
                        rgb = bgr_to_rgb_fill(frame_bgr, tw, th)
                    else:
                        rgb = np.concatenate([left, right], axis=1)
                else:
                    rgb = bgr_to_rgb_fill(frame_bgr, tw, th)
            except Exception:
                logger.exception("record tile draw failed")
                continue
            if src is not None:
                rgb = overlay_hud(rgb, hud_lines_for_source(slot, src))
            show_rgb(tile, rgb)

    # ── recording ────────────────────────────────────────────────────────

    def _set_busy(self, *, busy: bool, recording: bool = False) -> None:
        self.busy = busy
        for card in self.cards:
            card.set_locked(locked=busy or recording)
            # After unlock, force checkbox to match slot intent (never silently uncheck).
            if not busy and not recording:
                card.restore_bag_checkbox()
        enabled = not (busy or recording)
        for btn in (
            self.refresh_btn,
            self.add_btn,
            self.start_all_btn,
        ):
            btn.setEnabled(enabled)
        # Stop stays available while a preview is opening so it can cancel.
        self.stop_all_btn.setEnabled(not recording)
        self.out_entry.setEnabled(enabled)
        if recording:
            self.record_btn.setEnabled(False)
            self.stop_record_btn.setEnabled(not busy)
        elif busy:
            self.record_btn.setEnabled(False)
            self.stop_record_btn.setEnabled(False)
        else:
            self.stop_record_btn.setEnabled(False)
            self.refresh_record_gate()

    def _capture_bag_intent(self) -> dict[int, bool]:
        """Read bag checkboxes: RealSense SDK .db3/.bag or Elgato ROS2 *_color."""
        intent: dict[int, bool] = {}
        for card in self.cards:
            slot = self.session.slots[card.slot_id]
            want = bool(card.bag_check.isChecked())
            cam = slot.camera
            if cam is None:
                want = False
            elif cam.kind == "realsense" or cam.device_tag == "elgato":
                pass  # keep checkbox
            else:
                want = False
            intent[card.slot_id] = want
            slot.record_bag = want
        self.session.bag_intent = dict(intent)
        return intent

    def start_recording(self) -> None:
        if self.busy or self.session.is_recording:
            return
        ok, tip = self._armed_live_ready()
        if not ok:
            QMessageBox.information(self.root, "Not ready", tip)
            return
        if not self._prepare_high_rate_solo():
            return
        try:
            self._sync_all()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.root, "Recording", str(exc))
            return

        # Capture checkbox state BEFORE busy/lock — this is what Record will use.
        bag_intent = self._capture_bag_intent()
        rs_bag = []
        elgato_bag = []
        for i, want in bag_intent.items():
            if not want or not self.session.slots[i].armed:
                continue
            cam = self.session.slots[i].camera
            prefix = self.session.slots[i].prefix
            if cam and cam.kind == "realsense":
                rs_bag.append(prefix)
            elif cam and cam.device_tag == "elgato":
                elgato_bag.append(prefix)
        if rs_bag or elgato_bag:
            bits = []
            if rs_bag:
                bits.append(f"RealSense .db3/.bag: {', '.join(rs_bag)}")
            if elgato_bag:
                bits.append(f"Elgato ROS2 *_color: {', '.join(elgato_bag)}")
            self.set_status("Recording with " + " · ".join(bits))

        armed = [s for s in self.session.slots if s.armed]
        heavy = [
            s
            for s in armed
            if s.mode and s.mode.width * s.mode.height * s.mode.fps >= 1920 * 1080 * 60
        ]
        if len(armed) >= 2 and heavy:  # noqa: PLR2004
            self.set_status(
                "High-rate multi-cam — encode will finish every queued frame on Stop."
            )

        self._set_busy(busy=True)
        # Re-apply after lock so nothing in set_locked can drop the flag.
        for s in self.session.slots:
            s.record_bag = bag_intent.get(s.slot_id, False)
        self.set_status("Starting recording…")

        def worker() -> None:
            paths: list[Path] = []
            error: str | None = None
            try:
                paths = self.session.start_recording_armed(bag_intent=bag_intent)
            except Exception as exc:
                logger.exception("record start failed")
                error = str(exc)
            call_in_ui(lambda: self._record_started(paths, error))

        threading.Thread(target=worker, name="app-record-start", daemon=True).start()

    def _record_started(self, paths: list[Path], error: str | None) -> None:
        if error:
            self._set_busy(busy=False)
            for card in self.cards:
                card.restore_bag_checkbox()
            self.set_status("Recording did not start.")
            QMessageBox.critical(self.root, "Recording", error)
            return
        self._set_busy(busy=False, recording=True)
        n = max(1, len(paths))
        extra = self._elgato_live_lines()
        tip = f"Recording {n} camera(s)…"
        if extra:
            tip += "  " + "  ·  ".join(extra)
        self._set_record_tip(tip)
        bags = [
            s
            for s in self.session.slots
            if s.armed and self.session.bag_intent.get(s.slot_id)
        ]
        if bags:
            bits = []
            for s in bags:
                if s.camera and s.camera.kind == "realsense":
                    bits.append(f"{s.prefix} (.db3/.bag)")
                elif s.camera and s.camera.device_tag == "elgato":
                    bits.append(f"{s.prefix} (*_color ROS2)")
                else:
                    bits.append(s.prefix)
            self.set_status(
                f"Recording — bag ON for {', '.join(bits)}. Press Stop when finished."
            )
        else:
            self.set_status("Recording — press Stop when finished.")
        self.notebook.setCurrentWidget(self.record_page)

    def stop_recording(self) -> None:
        # Always allow Stop while a take is active — even if busy from a
        # hung start — otherwise the button appears to do nothing.
        if self._stopping:
            return
        if not self.session.is_recording:
            if self.busy:
                self._set_busy(busy=False)
            self.set_status("Not recording.")
            return

        self._stopping = True
        self._set_busy(busy=True, recording=True)
        self._set_record_tip("Stopping… saving files (please wait a moment).")
        self.set_status("Stopping recording…")

        def worker() -> None:
            reports: dict = {}
            error: str | None = None
            try:
                reports = self.session.stop_recording_armed()
            except Exception as exc:
                logger.exception("record stop failed")
                error = str(exc)
            call_in_ui(lambda: self._record_stopped(reports, error))

        threading.Thread(target=worker, name="app-record-stop", daemon=True).start()

    def _record_stopped(
        self, reports: dict[str, dict], error: str | None = None
    ) -> None:
        self._stopping = False
        self.last_reports = reports
        self._set_busy(busy=False)
        for card in self.cards:
            card.restore_bag_checkbox()
        if error:
            self.set_status(f"Stop failed: {error}")
            QMessageBox.critical(self.root, "Stop recording", error)
            self.refresh_record_gate()
            return

        valid = [r for r in reports.values() if "error" not in r]
        ok = bool(valid) and all(r.get("no_frame_drops") for r in valid)
        bag_ok = [
            (prefix, r)
            for prefix, r in reports.items()
            if r.get("bag_recorded")
        ]
        bag_missing = []
        for prefix, r in reports.items():
            if "error" in r:
                continue
            slot_id = next(
                (s.slot_id for s in self.session.slots if s.prefix == prefix), -1
            )
            if not self.session.bag_intent.get(slot_id, False):
                continue
            if not bag_file_missing(r):
                continue
            bag_missing.append(prefix)
        status = (
            "Saved cleanly — no frame drops."
            if ok
            else "Saved. Check Last report if something looks off."
        )
        hdmi = [r for r in valid if r.get("hdmi_not_120hz")]
        if hdmi:
            bits = []
            for report in hdmi:
                req = int(report.get("requested_fps") or 0)
                meas = float(report.get("measured_fps") or 0)
                stamp = report.get("container_fps")
                bits.append(capture_not_120_status(req, meas, stamp))
            status += " " + "; ".join(bits) + "."
        if any(r.get("r7_120_ok") for r in valid):
            status += " Elgato ~120 with no software drops."
        if bag_ok:
            status += f" .bag saved: {', '.join(p for p, _ in bag_ok)}."
        bag_drops = [
            (prefix, int(r.get("bag_dropped") or 0))
            for prefix, r in reports.items()
            if int(r.get("bag_dropped") or 0) > 0
        ]
        if bag_drops:
            status += (
                " Elgato ROS2 bag queue dropped frames: "
                + ", ".join(f"{p} x{n}" for p, n in bag_drops)
                + " (MP4 path separate)."
            )
        self.set_status(status)
        self._set_record_tip("Recording stopped. Arm cameras and start previews to record again.")
        if bag_missing:
            QMessageBox.critical(
                self.root,
                "RealSense .bag missing",
                "MP4 was saved, but .bag was checked and not written for:\n"
                + ", ".join(bag_missing)
                + "\n\nClose RealSense Viewer, use USB 3, keep .bag checked, Record again.",
            )

        no_capture = []
        # Only cameras that actually participated in THIS stop (reports keys).
        # Empty `{}` for unarmed slots used to false-trigger "0 frames" on RealSense
        # when only Elgato was armed.
        for prefix, report in reports.items():
            if "error" in report:
                continue
            if int(report.get("frames_read_by_camera") or 0) != 0:
                continue
            slot = next((s for s in self.session.slots if s.prefix == prefix), None)
            if slot is None:
                no_capture.append(f"prefix={prefix!r}")
                continue
            cam = slot.camera
            cam_label = getattr(cam, "name", None) or "unassigned"
            no_capture.append(
                f"Camera {slot.slot_id + 1} · prefix={slot.prefix!r} · {cam_label}"
            )
        if no_capture:
            QMessageBox.critical(
                self.root,
                "No frames captured",
                no_frames_captured_message(
                    no_capture, str(self.session.out_dir), BUILD_ID
                ),
            )

        mismatched = [
            (slot, reports.get(slot.prefix, {}))
            for slot in self.session.slots
            if reports.get(slot.prefix, {}).get("fps_mismatch")
            and not reports.get(slot.prefix, {}).get("hdmi_not_120hz")
            and int(reports.get(slot.prefix, {}).get("frames_read_by_camera") or 0) > 0
        ]
        # Auto-fix remaining mismatches (Elgato already auto-fixed in pipeline).
        if mismatched:
            details = "\n".join(
                f"• {slot.prefix}: requested {report.get('requested_fps', report.get('target_fps'))} fps, "
                f"delivered ~{float(report.get('measured_fps') or 0):.0f} fps "
                f"(file stamped {report.get('container_fps')} fps)"
                for slot, report in mismatched
            )
            self.set_status("Fixing playback timing for mismatched cameras…")
            self._convert_mismatched_async(mismatched)
            QMessageBox.information(
                self.root,
                "Playback timing",
                f"{details}\n\n"
                "Playback speed was auto-fixed to match real time.\n"
                "For true 120fps on Elgato: set the HDMI source itself to 1080p120.",
            )

        if not ok:
            drop_notes = []
            for slot in self.session.slots:
                report = reports.get(slot.prefix) or {}
                if report.get("no_frame_drops", True):
                    continue
                if report.get("hdmi_not_120hz"):
                    continue
                if int(report.get("frames_read_by_camera") or 0) == 0:
                    continue
                drop_notes.append(
                    f"{slot.prefix}: wrote {report.get('frames_written')} of "
                    f"{report.get('frames_read_by_camera')} frames "
                    f"(requested {report.get('requested_fps')}, "
                    f"measured ~{float(report.get('measured_fps') or 0):.0f})"
                )
            if drop_notes:
                QMessageBox.warning(
                    self.root,
                    "Some frames were skipped",
                    "Encode could not keep up (read ≠ written) on:\n\n"
                    + "\n".join(drop_notes)
                    + "\n\nHDMI ~60 is not this error. Keep companions at @30/@60; "
                    "only one 1080p@120 (Elgato) per take.",
                )

        saved: list[Path] = []
        for report in reports.values():
            raw = report.get("output_path")
            if not raw:
                continue
            path = Path(str(raw))
            if path.is_file() and path.suffix.lower() in {".mp4", ".avi", ".mkv"}:
                saved.append(path)
        if saved:
            show_review_prompt(
                self.root,
                saved,
                on_review=self.show_library,
                review_label="Open in Library",
            )

    def _convert_mismatched_async(self, mismatched: list[tuple[CameraSlot, dict]]) -> None:
        self._set_busy(busy=True)
        self.set_status("Fixing playback speed in the background…")

        def worker() -> None:
            results: dict[str, bool] = {}
            for slot, _report in mismatched:
                try:
                    results[slot.prefix] = bool(
                        slot.pipeline and slot.pipeline.convert_container_fps()
                    )
                except Exception:
                    logger.exception("FPS conversion failed for %s", slot.prefix)
                    results[slot.prefix] = False
            call_in_ui(lambda: self._conversion_done(results))

        threading.Thread(target=worker, name="app-fps-convert", daemon=True).start()

    def _conversion_done(self, results: dict[str, bool]) -> None:
        for slot in self.session.slots:
            if slot.pipeline and slot.pipeline.last_report:
                self.last_reports[slot.prefix] = slot.pipeline.last_report
        self._set_busy(busy=False)
        failed = [prefix for prefix, ok in results.items() if not ok]
        if failed:
            QMessageBox.warning(
                self.root,
                "Couldn't fix all files",
                "Still using original timing for: " + ", ".join(failed),
            )
        else:
            QMessageBox.information(
                self.root,
                "Speed fixed",
                "Playback timing was updated in the background. Frame count unchanged.",
            )
        self.set_status("Ready.")

    def show_report(self) -> None:
        popup = QDialog(self.root)
        popup.setWindowTitle("Last recording report")
        popup.resize(720, 520)
        popup.setObjectName("reportPopup")
        popup.setStyleSheet(f"QDialog#reportPopup {{ background: {BG}; }}")
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, on=True)
        popup.setModal(False)
        column = QVBoxLayout(popup)
        column.setContentsMargins(10, 10, 10, 8)
        column.setSpacing(10)
        text = QPlainTextEdit(popup)
        text.setFont(font(("Consolas", 9)))
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text.setStyleSheet(f"QPlainTextEdit {{ background: {PANEL}; color: {INK}; border: 1px solid {BORDER}; }}")
        column.addWidget(text, 1)
        controls = QHBoxLayout()
        column.addLayout(controls)
        paths = [
            (prefix, Path(str(report.get("output_path"))))
            for prefix, report in self.last_reports.items()
            if report.get("output_path")
        ]
        for prefix, path in paths:
            controls.addWidget(
                button(
                    popup,
                    f"Open {prefix}",
                    lambda p=path: self.show_library(p),
                    color=BLUE,
                )
            )
            controls.addSpacing(6)
        controls.addStretch(1)
        controls.addWidget(button(popup, "Close", popup.close, color="#475569"))
        summary = {
            prefix: {
                key: report.get(key)
                for key in (
                    "no_frame_drops",
                    "no_capture",
                    "r7_120_ok",
                    "hdmi_not_120hz",
                    "device_tag",
                    "frames_read_by_camera",
                    "frames_written",
                    "width",
                    "height",
                    "requested_fps",
                    "target_fps",
                    "measured_fps",
                    "fps_mismatch",
                    "container_fps",
                    "bag_recorded",
                    "bag_dropped",
                    "bag_frames_written",
                    "bag_path",
                    "output_path",
                )
                if key in report
            }
            if "error" not in report
            else report
            for prefix, report in self.last_reports.items()
        }
        text.setPlainText(json.dumps(summary, indent=2) if summary else "No recording yet.")
        text.setReadOnly(True)
        popup.show()

    def open_output_folder(self) -> None:
        path = Path(self.out_entry.text()).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                QMessageBox.critical(self.root, "Open folder", f"Could not open {path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.root, "Open folder", str(exc))

    def _tick(self) -> None:
        if self._closing:
            return
        page = self.notebook.currentWidget()
        # Only redraw the visible page — Setup+Record both converting HD frames
        # was freezing Tk ("Not Responding").
        if page is self.setup_page:
            for card in self.cards:
                try:
                    card.tick()
                except Exception:
                    logger.exception("preview tick failed for slot %s", getattr(card, "slot_id", "?"))
        elif page is self.record_page:
            try:
                self._tick_record_tiles()
            except Exception:
                logger.exception("record preview tick failed")
            if self.session.is_recording:
                extra = self._elgato_live_lines()
                if extra:
                    self._set_record_tip("Recording…  " + "  ·  ".join(extra))
            elif not self.busy:
                self.refresh_record_gate()
        delay_ms = 80
        QTimer.singleShot(delay_ms, self.root, self._tick)

    def _on_close(self) -> bool:
        if self.busy and self.session.is_recording:
            QMessageBox.warning(self.root, "Please wait", "Finish saving before closing.")
            return False
        if self.busy:
            request_preview_cancel()
            QMessageBox.warning(
                self.root,
                "Please wait",
                "A camera is still opening. Stop was requested. "
                "Wait until the preview clears, then close.",
            )
            return False
        if self.session.is_recording and not _ask_yes_no(
            self.root, "Recording", "Stop and save, then close?"
        ):
            return False
        self._closing = True
        try:
            self.library.stop_playback()
        except Exception:
            logger.debug("Could not stop the library playback", exc_info=True)
        try:
            self.session.shutdown()
        except Exception:
            logger.exception("shutdown failed")
        with contextlib.suppress(AttributeError, RuntimeError):
            QApplication.instance().removeEventFilter(self._wheel_filter)
        return True


# the number boxes of Qt follow the number format of Windows; a Thai format gives Thai digits, so the app uses the English format
def use_english_numbers() -> None:
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))


def main() -> None:
    silence_opencv()
    parser = argparse.ArgumentParser(description="POC1 Recorder — unified Setup / Record / Library")
    parser.add_argument(
        "--slots",
        type=int,
        default=None,
        help=f"Initial camera slots (>={settings.camera_slot_range[0]}); default: the Camera slots at start setting",
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="Save folder; default: the last Save folder")
    args = parser.parse_args()
    # A bad settings.json does not stop the start: the app uses the defaults and names each bad key.
    settings_error = ""
    try:
        load_settings(settings.settings_path)
    except ValidationError as exc:
        settings_error = "\n".join(f"{'.'.join(map(str, error['loc'])) or 'file'}: {error['msg']}" for error in exc.errors())
        logger.warning("Bad settings file %s, the defaults are used: %s", settings.settings_path, settings_error)
    # A value on the command line comes first, then the saved setting, then the default.
    slots = args.slots if args.slots is not None else settings.camera_slots
    if slots < settings.camera_slot_range[0]:
        parser.error(f"--slots must be >= {settings.camera_slot_range[0]}")
    out_dir = args.out_dir or (Path(settings.save_folder) if settings.save_folder else settings.default_save_folder)
    app = QApplication(sys.argv[:1])
    use_english_numbers()
    ui_bridge.install()
    root = AppWindow()
    UnifiedApp(root, n_slots=slots, out_dir=out_dir, settings_path=settings.settings_path)
    root.showMaximized()
    if settings_error:
        QMessageBox.warning(
            root,
            "Settings",
            f"{settings.settings_path} has a bad value, so the app uses the default settings.\n"
            f"The next change on the Settings tab replaces the file.\n\n{settings_error}",
        )
    app.exec()


if __name__ == "__main__":
    main()
