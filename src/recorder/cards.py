"""Setup-page camera card for the unified app."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget

logger = logging.getLogger("poc1.app.cards")

from recorder.config import settings  # noqa: E402
from recorder.devices import StreamMode, pick_auto_camera_for_slot  # noqa: E402
from recorder.preview_draw import bgr_to_rgb_fill, hud_lines_for_source, overlay_hud  # noqa: E402
from recorder.session import CameraSlot  # noqa: E402
from recorder.theme import (  # noqa: E402
    ACCENT,
    ACCENT_SOFT,
    BLUE,
    BORDER,
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
    set_combo_text,
    set_combo_values,
    set_label_colors,
    show_rgb,
    show_text,
    style_check_box,
    text_size,
)

if TYPE_CHECKING:
    from recorder.gui import UnifiedApp


class CameraCard(QFrame):
    """One camera lane: device, config, arm, optional RealSense .bag."""

    def __init__(self, master: QWidget, app: UnifiedApp, slot: CameraSlot):
        super().__init__(master)
        self.setObjectName(f"cameraCard{slot.slot_id}")
        self.setStyleSheet(f"QFrame#cameraCard{slot.slot_id} {{ background: {PANEL}; border: 1px solid {BORDER}; }}")
        # The Tk grid gives each card its half of the page and cuts off what does not fit.
        self.setMinimumWidth(1)
        self.app = app
        self.session = app.session
        self.slot_id = slot.slot_id
        self._cam_by_label: dict[str, str] = {}
        self._mode_by_label: dict[str, StreamMode] = {}

        column = QVBoxLayout(self)
        column.setContentsMargins(1, 1, 1, 1)
        column.setSpacing(0)

        head, head_row = frame(self, SURFACE, padx=14, pady=10, kind="h")
        column.addWidget(head)
        head_row.addWidget(label(head, f"Camera {slot.slot_id + 1}", fg=INK, font_spec=("Segoe UI Semibold", 12)))
        head_row.addSpacing(10)
        self.badge = label(head, "Idle", fg=INK, bg="#e2e8f0", font_spec=("Segoe UI Semibold", 8), padx=8, pady=2)
        head_row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head_row.addStretch(1)
        self.remove_btn = button(
            head,
            "Remove",
            lambda: app.remove_camera(self.slot_id),
            color="#991b1b",
            padx=10,
            pady=4,
        )
        head_row.addWidget(self.remove_btn)
        if len(self.session.slots) <= settings.camera_slot_range[0]:
            self.remove_btn.setEnabled(False)

        form, grid = frame(self, PANEL, padx=14, pady=10, kind="grid")
        column.addWidget(form)
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(6)

        self._label(form, grid, "Device", 0)
        self.device_combo = NoWheelComboBox(form)
        grid.addWidget(self.device_combo, 0, 1, 1, 3)
        self.device_combo.activated.connect(lambda _index: self._on_device())

        self._label(form, grid, "Configuration", 1)
        self.mode_combo = NoWheelComboBox(form)
        grid.addWidget(self.mode_combo, 1, 1, 1, 3)
        self.mode_combo.activated.connect(lambda _index: self._on_mode())

        self._label(form, grid, "File prefix", 2)
        self.prefix_entry = QLineEdit(slot.prefix, form)
        self.prefix_entry.setFont(font(("Segoe UI", 9)))
        text_size(self.prefix_entry, chars=12, pad=10)
        grid.addWidget(self.prefix_entry, 2, 1, Qt.AlignmentFlag.AlignLeft)
        self.prefix_entry.textChanged.connect(lambda _text: self._sync_prefix())

        # R5 — arming (Setup only; Library has no arm checkbox by design).
        self.arm_check = QCheckBox("Armed (record this camera)", form)
        self.arm_check.setFont(font(("Segoe UI Semibold", 9)))
        style_check_box(self.arm_check)
        self.arm_check.setChecked(bool(slot.armed))
        self.arm_check.clicked.connect(lambda _checked: self._sync_arm())
        grid.addWidget(self.arm_check, 2, 2, 1, 2, Qt.AlignmentFlag.AlignLeft)

        # Written on Record only (never on Start preview). New SDKs save .db3.
        self.bag_check = QCheckBox("Also save bag with MP4 (RealSense: Intel .db3/.bag · Elgato: ROS2 *_color)", form)
        self.bag_check.setFont(font(("Segoe UI Semibold", 9)))
        style_check_box(self.bag_check)
        self.bag_check.setChecked(bool(slot.record_bag))
        self.bag_check.clicked.connect(lambda _checked: self._sync_bag())
        self.bag_check.setMinimumWidth(1)
        grid.addWidget(self.bag_check, 3, 1, 1, 3, Qt.AlignmentFlag.AlignLeft)
        note = label(
            form,
            "On Record only. RealSense: Intel SDK .db3/.bag (USB3, close Viewer). "
            "Elgato: ROS2 color folder *_color (not Intel .bag). "
            "For true 120fps set mirrorless HDMI to 1080p120.",
            fg=MUTED,
            font_spec=("Segoe UI", 8),
        )
        grid.addWidget(note, 4, 1, 1, 3, Qt.AlignmentFlag.AlignLeft)

        shell_box = QWidget(self)
        shell_row = QHBoxLayout(shell_box)
        shell_row.setContentsMargins(14, 0, 14, 8)
        self.preview_shell, shell_layout = frame(shell_box, "#0b1220", border=True)
        self.preview_shell.setFixedHeight(260)
        shell_row.addWidget(self.preview_shell)
        column.addWidget(shell_box)
        self.preview = image_label(self.preview_shell, "Start preview to see live video")
        shell_layout.setContentsMargins(1, 1, 1, 1)
        shell_layout.addWidget(self.preview)

        actions, actions_row = frame(self, PANEL, padx=14, pady=8, kind="h")
        column.addWidget(actions)
        self.start_btn = button(actions, "Start preview", self.start_preview, color=BLUE)
        actions_row.addWidget(self.start_btn)
        actions_row.addSpacing(6)
        self.stop_btn = button(actions, "Stop preview", self.stop_preview, color="#475569")
        actions_row.addWidget(self.stop_btn)
        self.status_label = label(
            actions, slot.status, fg=MUTED, font_spec=("Segoe UI", 9), align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        actions_row.addSpacing(6)
        actions_row.addWidget(self.status_label, 1)

    @staticmethod
    def _label(master: QWidget, grid, text: str, row: int) -> None:
        widget = label(master, text, fg=MUTED, font_spec=("Segoe UI", 9))
        text_size(widget, chars=12, fixed=False)
        grid.addWidget(widget, row, 0, Qt.AlignmentFlag.AlignLeft)

    def load_devices(self, auto_assign: bool = False) -> None:
        labels = [d.label() for d in self.session.devices]
        self._cam_by_label = {d.label(): d.cam_id for d in self.session.devices}
        set_combo_values(self.device_combo, ["Select a camera…", *labels])
        slot = self.session.slots[self.slot_id]

        if slot.camera:
            set_combo_text(self.device_combo, slot.camera.label())
            self._load_modes(slot)
            self._refresh_bag_enabled()
            return

        if auto_assign:
            used = {
                s.camera.cam_id
                for s in self.session.slots
                if s.slot_id != self.slot_id and s.camera is not None
            }
            candidate = pick_auto_camera_for_slot(
                self.slot_id, self.session.devices, used
            )
            if candidate:
                set_combo_text(self.device_combo, candidate.label())
                self._on_device()
                return
        set_combo_text(self.device_combo, "Select a camera…")
        set_combo_text(self.mode_combo, "Select configuration…")
        self._refresh_bag_enabled()

    def _load_modes(self, slot: CameraSlot) -> None:
        self._mode_by_label = {m.label(): m for m in slot.available_modes}
        set_combo_values(self.mode_combo, list(self._mode_by_label))
        if slot.mode:
            set_combo_text(self.mode_combo, slot.mode.label())

    def sync_opened_mode_from_source(self) -> None:
        """Sync Setup dropdown only if open fell back to a different resolution.

        Keep the user-selected FPS in the combobox (HUD shows stamped/delivery).
        Overwriting @120 → @60 after a short sample made operators think 120 was
        unavailable even when OBS held 120 on the same card.
        """
        slot = self.session.slots[self.slot_id]
        if slot.pipeline is None:
            return
        src = slot.pipeline.source
        if getattr(src, "device_tag", "") != "elgato":
            return
        w = int(getattr(src, "width", 0) or 0)
        h = int(getattr(src, "height", 0) or 0)
        fmt = str(getattr(src, "pixel_format", "mjpg") or "mjpg")
        if w < 1 or h < 1:
            return
        req = int(getattr(src, "requested_fps", 0) or 0)
        stamp = int(getattr(src, "target_fps", 0) or 0)
        # Prefer showing what the operator selected for FPS.
        fps = req if req > 0 else stamp
        if fps < 1:
            return
        if slot.mode is not None and slot.mode.width == w and slot.mode.height == h:
            # Same size — leave dropdown FPS as the user-selected mode.
            return
        opened = StreamMode(w, h, fps, fmt)
        if slot.mode == opened:
            return
        modes = list(slot.available_modes)
        if opened not in modes:
            modes.insert(0, opened)
            slot.available_modes = modes
        slot.mode = opened
        self._load_modes(slot)

    def _on_device(self, *_args) -> None:
        if self.app.busy:
            return
        cam_id = self._cam_by_label.get(combo_text(self.device_combo))
        if not cam_id:
            self._refresh_bag_enabled()
            return
        try:
            slot = self.session.assign_camera(self.slot_id, cam_id)
            # A Tk StringVar trace runs once on set(); textChanged would not run for the same text.
            blocked = self.prefix_entry.blockSignals(True)
            self.prefix_entry.setText(slot.prefix)
            self.prefix_entry.blockSignals(blocked)
            self._sync_prefix()
            self.bag_check.setChecked(bool(slot.record_bag))
            self._load_modes(slot)
            self._refresh_bag_enabled()
            show_text(self.preview, "Start preview to see live video")
            self.app.refresh_record_gate()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.app.root, "Camera", str(exc))
            self.load_devices()

    def _on_mode(self, *_args) -> None:
        if self.app.busy:
            return
        mode = self._mode_by_label.get(combo_text(self.mode_combo))
        if mode is None:
            return
        try:
            self.session.set_mode(self.slot_id, mode)
            show_text(self.preview, "Configuration changed — start preview")
            self.app.refresh_record_gate()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self.app.root, "Configuration", str(exc))

    def _sync_prefix(self) -> None:
        self.session.set_prefix(self.slot_id, self.prefix_entry.text())

    def _sync_arm(self) -> None:
        self.session.set_armed(self.slot_id, self.arm_check.isChecked())
        self.app.refresh_record_gate()

    def set_armed_ui(self, armed: bool) -> None:
        self.arm_check.setChecked(bool(armed))
        self.session.set_armed(self.slot_id, bool(armed))

    def _sync_bag(self) -> None:
        self.session.slots[self.slot_id].record_bag = self.bag_check.isChecked()

    def restore_bag_checkbox(self) -> None:
        """Keep the checkbox matching the slot intent after busy/unlock."""
        slot = self.session.slots[self.slot_id]
        want = bool(slot.record_bag) or bool(
            getattr(self.session, "bag_intent", {}).get(self.slot_id, False)
        )
        if slot.camera is None or not (
            slot.camera.kind == "realsense" or slot.camera.device_tag == "elgato"
        ):
            want = False
        slot.record_bag = want
        try:
            self.bag_check.setChecked(want)
            self._refresh_bag_enabled(locked=False)
        except RuntimeError:
            pass

    def _refresh_bag_enabled(self, locked: bool = False) -> None:
        slot = self.session.slots[self.slot_id]
        is_bag = slot.camera is not None and (
            slot.camera.kind == "realsense" or slot.camera.device_tag == "elgato"
        )
        if not is_bag:
            if self.bag_check.isChecked():
                self.bag_check.setChecked(False)
            slot.record_bag = False
        try:
            self.bag_check.setEnabled(not (locked or not is_bag))
        except RuntimeError:
            pass

    def sync_controls(self) -> None:
        self._sync_prefix()
        self._sync_arm()
        self._sync_bag()

    def start_preview(self) -> None:
        if self.app.busy:
            return
        self.app.start_slot_preview_async(self.slot_id)

    def _warn_if_no_preview_frames(self) -> None:
        slot = self.session.slots[self.slot_id]
        if slot.pipeline is None or slot.get_preview_frame() is not None:
            return
        QMessageBox.warning(
            self.app.root,
            f"Camera {self.slot_id + 1}",
            "Opened, but no image yet. Check the cable / HDMI signal, then Start preview again.",
        )

    def stop_preview(self) -> None:
        if self.app.busy:
            self.app.stop_all_previews()
            return
        self.session.stop_slot_preview(self.slot_id)
        show_text(self.preview, "Preview stopped")
        self.app.refresh_record_gate()

    def set_locked(self, locked: bool) -> None:
        self.device_combo.setEnabled(not locked)
        self.mode_combo.setEnabled(not locked)
        self.prefix_entry.setEnabled(not locked)
        self.arm_check.setEnabled(not locked)
        self._refresh_bag_enabled(locked=locked)
        self.remove_btn.setEnabled(not (locked or len(self.session.slots) <= settings.camera_slot_range[0]))

    # Set the stylesheet only on a change, because the tick runs every 80 ms.
    def _set_badge(self, text: str, fg: str, bg: str) -> None:
        if self.badge.text() == text:
            return
        self.badge.setText(text)
        set_label_colors(self.badge, fg, bg, padx=8, pady=2)

    def tick(self) -> None:
        slot = self.session.slots[self.slot_id]
        status = slot.status
        src = slot.pipeline.source if slot.pipeline else None
        if src is not None and getattr(src, "device_tag", "") == "elgato":
            measured = float(getattr(src, "actual_fps", 0) or 0)
            wanted = int(
                getattr(src, "requested_fps", 0) or getattr(src, "target_fps", 0) or 0
            )
            stamp = int(getattr(src, "target_fps", 0) or 0)
            if measured > 1:
                status = f"{status} | delivery ~{measured:.0f}fps"
                if wanted >= settings.high_rate_fps and measured < wanted * 0.85:
                    from recorder.user_messages import capture_not_120_status

                    status += (
                        f" ({capture_not_120_status(wanted, measured, stamp)})"
                    )
        try:
            self.status_label.setText(status)
            if slot.pipeline and slot.pipeline.camera_handler.is_recording:
                self._set_badge("Recording", "#7f1d1d", "#fecaca")
            elif slot.pipeline:
                self._set_badge("Live", ACCENT, ACCENT_SOFT)
            else:
                self._set_badge("Idle", INK, "#e2e8f0")
        except RuntimeError:
            return

        frame_bgr = slot.get_preview_frame()
        if frame_bgr is None:
            return
        tw = max(self.preview_shell.width() - 4, 280)
        th = max(self.preview_shell.height() - 4, 160)
        depth = slot.get_depth_preview_frame()
        try:
            if depth is not None and getattr(src, "device_tag", "") == "realsense":
                half = max(tw // 2, 80)
                left = bgr_to_rgb_fill(frame_bgr, half, th)
                right = bgr_to_rgb_fill(depth, max(tw - half, 80), th)
                if left.shape[0] != right.shape[0] or left.ndim != right.ndim:
                    rgb = bgr_to_rgb_fill(frame_bgr, tw, th)
                else:
                    rgb = np.concatenate([left, right], axis=1)
            else:
                rgb = bgr_to_rgb_fill(frame_bgr, tw, th)
        except Exception:
            logger.exception("preview draw failed for slot %d", self.slot_id)
            return
        if src is not None:
            try:
                rgb = overlay_hud(rgb, hud_lines_for_source(slot, src))
            except Exception:
                logger.exception("HUD overlay failed for slot %d", self.slot_id)
        show_rgb(self.preview, rgb)
