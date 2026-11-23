"""Library page — browse, play, export (R9–R10)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from recorder.theme import (
    ACCENT,
    BG,
    BLUE,
    BORDER,
    FIELD,
    GREEN,
    INK,
    MUTED,
    PANEL,
    SURFACE,
    NoWheelComboBox,
    button,
    combo_text,
    font,
    frame,
    image_label,
    label,
    set_label_colors,
    show_rgb,
    show_text,
    tk_default_font,
)
from recorder.export import export_to_mp4, list_media_files
from recorder.device_enum import quiet_opencv
from recorder.ui_bridge import call_in_ui

if TYPE_CHECKING:
    from recorder.gui import UnifiedApp


def _ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(
        parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    return answer == QMessageBox.StandardButton.Yes


class LibraryPage(QFrame):
    def __init__(self, master: QWidget, app: "UnifiedApp"):
        super().__init__(master)
        self.setObjectName("libraryPage")
        self.setStyleSheet(f"QFrame#libraryPage {{ background: {BG}; }}")
        self.app = app
        self.files: list[Path] = []
        self.selected: Optional[Path] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._draw_next)
        self._paused = False
        self._busy = False
        self._delay = 33
        self._build()

    def _build(self) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        tip, tip_layout = frame(self, SURFACE, padx=16, pady=10)
        column.addWidget(tip)
        tip_layout.addWidget(
            label(
                tip,
                "Library: select MP4 → Play. Select RealSense .db3/.bag → Export to MP4 → Play. "
                "Record saves MP4 + .db3 in the folder; JSON/CSV are in meta/.",
                fg=MUTED,
                font_spec=("Segoe UI", 9),
                wrap=True,
            )
        )

        bar, bar_row = frame(self, BG, padx=16, pady=8, kind="h")
        column.addWidget(bar)
        bar_row.addWidget(label(bar, "Folder", fg=MUTED, font_spec=("Segoe UI Semibold", 9)))
        bar_row.addSpacing(8)
        self.folder_entry = QLineEdit(bar)
        self.folder_entry.setFont(font(("Segoe UI", 9)))
        bar_row.addWidget(self.folder_entry, 1)
        bar_row.addSpacing(8)
        bar_row.addWidget(button(bar, "Browse…", self.browse_folder, color="#475569"))
        bar_row.addSpacing(6)
        bar_row.addWidget(button(bar, "Refresh", self.refresh_list, color=BLUE))

        body, body_grid = frame(self, BG, padx=16, pady=8, kind="grid")
        column.addWidget(body, 1)
        body_grid.setColumnStretch(1, 1)
        body_grid.setRowStretch(0, 1)
        body_grid.setHorizontalSpacing(10)

        left, left_column = frame(body, PANEL, border=True)
        body_grid.addWidget(left, 0, 0)
        left_column.setContentsMargins(1, 1, 1, 1)
        left_column.addWidget(label(left, "Media", fg=INK, font_spec=("Segoe UI Semibold", 10), padx=10, pady=8))
        self.listbox = QListWidget(left)
        self.listbox.setFont(font(("Segoe UI", 9)))
        self.listbox.setStyleSheet(
            f"QListWidget {{ background: {FIELD}; color: {INK}; border: 0; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}; color: #ffffff; }}"
        )
        self.listbox.setFixedWidth(self.listbox.fontMetrics().horizontalAdvance("0") * 36 + 4)
        self.listbox.setMinimumHeight(self.listbox.fontMetrics().lineSpacing() * 22)
        inner = QWidget(left)
        inner_column = QVBoxLayout(inner)
        inner_column.setContentsMargins(8, 0, 8, 10)
        inner_column.setSpacing(2)
        inner_column.addWidget(self.listbox, 1)
        inner_column.addSpacing(8)
        inner_column.addWidget(button(inner, "Play", self.play_selected, color=GREEN))
        inner_column.addWidget(button(inner, "Open in system player", self.open_system_player, color="#475569"))
        left_column.addWidget(inner, 1)
        self.listbox.itemSelectionChanged.connect(self._on_select)

        right = QWidget(body)
        body_grid.addWidget(right, 0, 1)
        right_grid = QGridLayout(right)
        right_grid.setContentsMargins(0, 0, 0, 0)
        right_grid.setSpacing(0)
        right_grid.setRowStretch(0, 1)
        right_grid.setColumnStretch(0, 1)

        self.video = image_label(right, "Select a video to play", size=11)
        right_grid.addWidget(self.video, 0, 0)

        controls, controls_row = frame(right, PANEL, padx=12, pady=8, kind="h")
        right_grid.addWidget(controls, 1, 0)
        self.info_label = label(controls, "No file selected", fg=INK, font_spec=("Segoe UI", 9))
        controls_row.addWidget(self.info_label)
        controls_row.addStretch(1)
        controls_row.addWidget(button(controls, "Stop", self.stop_playback, color="#991b1b"))
        controls_row.addSpacing(6)
        self.pause_btn = button(controls, "Pause", self.toggle_pause, color="#475569")
        controls_row.addWidget(self.pause_btn)

        export = QGroupBox(" Export to MP4 (H.264 or H.265 when available) ", right)
        export.setFont(font(("Segoe UI Semibold", 9)))
        export.setStyleSheet(
            f"QGroupBox {{ background: {PANEL}; color: {INK}; border: 1px solid {BORDER}; margin-top: 10px; }}"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 2px; }"
        )
        right_grid.addWidget(export, 2, 0)
        export_grid = QGridLayout(export)
        export_grid.setContentsMargins(12, 14, 12, 10)
        export_grid.setHorizontalSpacing(8)
        codec_label = label(export, "Codec", fg=MUTED)
        codec_label.setFont(tk_default_font())
        export_grid.addWidget(codec_label, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self.codec_combo = NoWheelComboBox(export)
        self.codec_combo.addItems(["h264", "h265"])
        self.codec_combo.setFixedWidth(self.codec_combo.fontMetrics().horizontalAdvance("0") * 10 + 30)
        export_grid.addWidget(self.codec_combo, 0, 1, Qt.AlignmentFlag.AlignLeft)
        export_grid.addWidget(button(export, "Export selected", self.export_selected, color=BLUE), 0, 2)
        export_grid.addWidget(button(export, "Choose file…", self.export_browse, color="#475569"), 0, 3, Qt.AlignmentFlag.AlignLeft)
        export_grid.setColumnStretch(4, 1)
        export_grid.addWidget(
            label(
                export,
                "Export selected bag → NEW {name}_h264.mp4 (decoded). "
                "RealSense = .db3/.bag via SDK. Elgato = *_color folder. "
                "Record MP4 is kept. Install ffmpeg for fallbacks.",
                fg=MUTED,
                font_spec=("Segoe UI", 8),
                wrap=True,
            ),
            1,
            0,
            1,
            5,
        )
        self.export_status = label(export, "", fg=MUTED, font_spec=("Segoe UI", 8))
        export_grid.addWidget(self.export_status, 2, 0, 1, 5)
        export_grid.setVerticalSpacing(6)
        right_grid.setVerticalSpacing(8)

    def _set_export_status(self, text: str, fg: Optional[str] = None) -> None:
        self.export_status.setText(text)
        if fg is not None:
            set_label_colors(self.export_status, fg)

    def sync_folder(self, folder: Path) -> None:
        self.folder_entry.setText(str(Path(folder).resolve()))
        self.refresh_list()
        try:
            from recorder.export import _find_ffmpeg

            if _find_ffmpeg() is None:
                self._set_export_status(
                    "Note: ffmpeg not on PATH — install it for export fallbacks / Elgato names."
                )
        except Exception:  # noqa: BLE001
            pass

    def browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self.window(), "", self.folder_entry.text() or ".")
        if chosen:
            self.folder_entry.setText(chosen)
            self.refresh_list()

    def refresh_list(self) -> None:
        folder = Path(self.folder_entry.text() or ".")
        self.files = list_media_files(folder)
        blocked = self.listbox.blockSignals(True)
        self.listbox.clear()
        for path in self.files:
            self.listbox.addItem(self._display_name(path))
        self.listbox.blockSignals(blocked)
        self.app.set_status(f"Library: {len(self.files)} file(s) in {folder}")

    @staticmethod
    def _display_name(path: Path) -> str:
        """Clear Library labels so *_color is not mistaken for a playable video."""
        path = Path(path)
        if path.is_dir() and (
            path.name.endswith("_color") or (path / "metadata.yaml").is_file()
        ):
            return f"{path.name}  [ROS2 bag — Export / Play sibling MP4]"
        if path.suffix.lower() in {".db3", ".bag", ".bd3"}:
            return f"{path.name}  [bag — Export / Play sibling MP4]"
        return path.name

    def _current_index(self) -> Optional[int]:
        rows = self.listbox.selectedIndexes()
        if not rows:
            return None
        return rows[0].row()

    def select_path(self, path: Path) -> None:
        path = Path(path)
        self.sync_folder(path.parent)
        try:
            idx = self.files.index(path)
        except ValueError:
            self.refresh_list()
            try:
                idx = self.files.index(path)
            except ValueError:
                return
        # A Tk selection_set does not send <<ListboxSelect>>.
        blocked = self.listbox.blockSignals(True)
        self.listbox.clearSelection()
        self.listbox.setCurrentRow(idx)
        self.listbox.blockSignals(blocked)
        self.listbox.scrollToItem(self.listbox.item(idx))
        self.selected = path
        self.info_label.setText(str(path))
        if path.suffix.lower() in {".mp4", ".avi", ".mkv"}:
            self.play_selected()

    def _on_select(self, *_args) -> None:
        idx = self._current_index()
        if idx is None:
            return
        self.selected = self.files[idx]
        self.info_label.setText(str(self.selected))

    def play_selected(self) -> None:
        if self.selected is None:
            idx = self._current_index()
            if idx is None:
                QMessageBox.information(self.window(), "Playback", "Select a media file first.")
                return
            self.selected = self.files[idx]
        path = self.selected

        # Elgato ROS2 bag folder (*_color) — not a playable video.
        if path.is_dir() and (
            path.name.endswith("_color") or (path / "metadata.yaml").is_file()
        ):
            sibling = self._sibling_record_mp4_for_bag(path)
            if sibling is not None:
                if _ask_yes_no(
                    self.window(),
                    "Playback",
                    f"{path.name} is an Elgato ROS2 color bag folder "
                    "(m_*_color with .db3 + metadata.yaml — not a video).\n\n"
                    f"Play the Record MP4 instead?\n{sibling.name}\n\n"
                    "Yes = play MP4.  No = stay here.\n"
                    "To decode the bag: select this folder → Export selected.",
                ):
                    self._start_playback(sibling)
                return
            if _ask_yes_no(
                self.window(),
                "Playback",
                f"{path.name} is a ROS2 bag folder (not playable video).\n\n"
                "Export it to a NEW MP4 now?",
            ):
                self._run_export(path)
            return

        if path.suffix.lower() in {".bag", ".bd3", ".db3"}:
            sibling = path.with_suffix(".mp4")
            if sibling.is_file() and sibling.stat().st_size > 32:
                if _ask_yes_no(
                    self.window(),
                    "Playback",
                    f"{path.name} is a bag/SDK file and cannot play directly.\n\n"
                    f"A matching Record MP4 exists:\n{sibling.name}\n\n"
                    "Play that original MP4 now?\n\n"
                    "(Cancel = stay here. Export selected decodes the .db3 to a NEW mp4 "
                    "and does not replace the Record file.)",
                ):
                    self._start_playback(sibling)
                return
            if _ask_yes_no(
                self.window(),
                "Playback",
                f"{path.name} cannot play directly.\n\n"
                "Export it to a NEW MP4 (decoded from the bag; Record MP4 is kept), then play?",
            ):
                self._run_export(path)
            return
        self._start_playback(path)

    @staticmethod
    def _sibling_record_mp4_for_bag(path: Path) -> Optional[Path]:
        """Record MP4 beside a RealSense .db3 or Elgato *_color folder."""
        path = Path(path)
        candidates: list[Path] = []
        if path.is_dir():
            name = path.name
            if name.endswith("_color"):
                candidates.append(path.parent / f"{name[:-6]}.mp4")
            candidates.append(path.parent / f"{path.name}.mp4")
        else:
            candidates.append(path.with_suffix(".mp4"))
            stem = path.stem
            if stem.endswith("_color"):
                candidates.append(path.parent / f"{stem[:-6]}.mp4")
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_size > 32:
                return candidate
        return None

    def open_system_player(self) -> None:
        if self.selected is None:
            return
        path = self.selected
        if path.suffix.lower() not in {".mp4", ".avi", ".mkv"}:
            QMessageBox.information(self.window(), "System player", "Open an MP4 (export bags first if needed).")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.window(), "System player", str(exc))

    def _start_playback(self, path: Path) -> None:
        self.stop_playback()
        with quiet_opencv():
            cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            QMessageBox.critical(self.window(), "Playback", f"Could not open:\n{path}")
            return
        self._cap = cap
        self._paused = False
        self.pause_btn.setText("Pause")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 30.0
        self._delay = max(1, int(1000 / min(fps, 120.0)))
        self.info_label.setText(f"{path.name}  •  {fps:.1f} fps")
        self.app.set_status(f"Playing {path.name}")
        self._draw_next()

    def _draw_next(self) -> None:
        if self._cap is None:
            return
        if not self._paused:
            ok, frame_bgr = self._cap.read()
            if not ok:
                self.app.set_status("Playback finished")
                return
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            max_w = max(self.video.width(), 320)
            max_h = max(self.video.height(), 240)
            scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
            if scale < 1.0:
                rgb = cv2.resize(
                    rgb,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            show_rgb(self.video, rgb)
        self._timer.start(self._delay)

    def toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_btn.setText("Resume" if self._paused else "Pause")

    def stop_playback(self) -> None:
        self._timer.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        show_text(self.video, "Playback stopped")

    def export_selected(self) -> None:
        if self.selected is None:
            idx = self._current_index()
            if idx is None:
                QMessageBox.information(self.window(), "Export", "Select a .bag / .bd3 / .db3 (or video) file.")
                return
            self.selected = self.files[idx]
        self._run_export(self.selected)

    def export_browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self.window(),
            "",
            self.folder_entry.text() or ".",
            "Bags / video (*.bag *.bd3 *.db3 *.mp4 *.avi *.mkv);;All files (*.*)",
        )
        if path:
            self._run_export(Path(path))

    def _run_export(self, source: Path) -> None:
        if self._busy:
            return
        codec = combo_text(self.codec_combo).strip().lower() or "h264"
        out = source.with_name(f"{source.stem}_{codec}.mp4")
        self._busy = True
        self._set_export_status(f"Exporting {source.name}…", ACCENT)

        def worker() -> None:
            result = export_to_mp4(
                source,
                out,
                codec=codec,
                on_progress=lambda m: call_in_ui(lambda: self._set_export_status(m)),
            )
            call_in_ui(lambda: self._export_done(result))

        threading.Thread(target=worker, name="app-export", daemon=True).start()

    def _export_done(self, result) -> None:
        self._busy = False
        if not result.ok:
            self._set_export_status(result.message, "#b91c1c")
            QMessageBox.critical(self.window(), "Export failed", result.message)
            return
        self._set_export_status(f"{result.message}  ({result.codec_label})", GREEN)
        self.refresh_list()
        if result.output_path and result.output_path.is_file():
            if _ask_yes_no(self.window(), "Export complete", "Play the new MP4 now?"):
                self.select_path(result.output_path)
