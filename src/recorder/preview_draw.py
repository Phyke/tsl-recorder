"""Live-preview scaling: fill the widget (cover-crop) without blocking Record."""

import math
from typing import TYPE_CHECKING

import cv2
import numpy as np

from recorder.config import PreviewSettings, settings
from recorder.frame_layout import PIPE_NV12, ensure_bgr
from recorder.frame_source import BAR_H
from recorder.user_messages import capture_not_120_hud_hint

if TYPE_CHECKING:
    from recorder.devices import ConnectedCamera
    from recorder.frame_source import FrameSource
    from recorder.session import CameraSlot


# The camera types of the Preview settings: RealSense, Elgato, and Other (webcam, virtual, RealSense as UVC, fake).
def preview_settings_for(camera: ConnectedCamera | None) -> PreviewSettings:
    if camera is not None and camera.kind == "realsense":
        return settings.preview_realsense
    if camera is not None and camera.device_tag == "elgato":
        return settings.preview_elgato
    return settings.preview_other


def colorize_depth_preview(raw: np.ndarray) -> np.ndarray:
    """Jet preview of a uint16 depth buffer. Not used on the capture thread."""
    if raw.ndim != 2:  # noqa: PLR2004
        return ensure_bgr(raw)
    vis = cv2.convertScaleAbs(raw, alpha=255.0 / 4000.0)
    return cv2.applyColorMap(vis, cv2.COLORMAP_JET)


def downscale_for_preview(frame: np.ndarray, scale_percent: int) -> np.ndarray:
    bgr = ensure_bgr(frame)
    if scale_percent >= 100:  # noqa: PLR2004
        return bgr.copy()
    h, w = bgr.shape[:2]
    nw = max(1, w * scale_percent // 100)
    nh = max(1, h * scale_percent // 100)
    return cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)


def fill_bgr(bgr: np.ndarray, dest_w: int, dest_h: int) -> np.ndarray:
    """Scale to cover dest_w x dest_h, then center-crop (no black bars)."""
    dest_w = max(int(dest_w), 1)
    dest_h = max(int(dest_h), 1)
    h, w = bgr.shape[:2]
    if w < 1 or h < 1:
        return np.zeros((dest_h, dest_w, 3), dtype=np.uint8)
    scale = max(dest_w / float(w), dest_h / float(h))
    nw = max(1, round(w * scale))
    nh = max(1, round(h * scale))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    x0 = max(0, (nw - dest_w) // 2)
    y0 = max(0, (nh - dest_h) // 2)
    crop = resized[y0 : y0 + dest_h, x0 : x0 + dest_w]
    if crop.shape[0] != dest_h or crop.shape[1] != dest_w:
        return cv2.resize(crop, (dest_w, dest_h), interpolation=cv2.INTER_LINEAR)
    return crop


def bgr_to_rgb_fill(frame: np.ndarray, dest_w: int, dest_h: int) -> np.ndarray:
    bgr = ensure_bgr(frame)
    return cv2.cvtColor(fill_bgr(bgr, dest_w, dest_h), cv2.COLOR_BGR2RGB)


def overlay_hud(rgb: np.ndarray, lines: list[str], prefs: PreviewSettings, top: int = 0) -> np.ndarray:
    """Viewer-style status on a preview tile (display only)."""
    if rgb is None or not lines:
        return rgb
    img = np.ascontiguousarray(rgb)
    # The values at 18 px per line (first line 20 px below the top, font scale 0.48, line width 1) scale with the HUD font size.
    size = prefs.font_px / PreviewSettings.hud_base_font_px
    color = PreviewSettings.hud_colors[prefs.color]
    font_scale = 0.48 * size
    thickness = max(1, round(size))
    texts = [str(line)[:80] for line in lines]
    sizes = [cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness) for text in texts]
    y = top + round(20 * size)
    # A dark box behind the text (a light box behind black text), in the top-right corner, away from the barcode at the
    # top left. The box starts below the `top` rows. The longest line ends 8 px from the tile edge, and the box edge is
    # `pad` px left of the text (the mirror of the old top-left box).
    pad = max(2, round(4 * size))
    box_color = (255, 255, 255) if prefs.color == "black" else (0, 0, 0)
    box_top = max(top, y - max(h for (_w, h), _b in sizes) - pad)
    box_bottom = y + prefs.font_px * (len(texts) - 1) + max(b for _wh, b in sizes) + pad
    text_x = max(pad, img.shape[1] - 8 - max(w for (w, _h), _b in sizes))
    cv2.rectangle(img, (text_x - pad - 1, box_top), (img.shape[1], box_bottom), box_color, cv2.FILLED)
    for text in texts:
        cv2.putText(img, text, (text_x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, thickness, cv2.LINE_AA)
        y += prefs.font_px
    return img


# The barcode rows at the top of a fake camera frame (BAR_H), as tile rows after the scale and the crop of fill_bgr.
# 0 when the crop cuts the barcode off. A frame from a real camera has no barcode, but the HUD keeps the same place.
def barcode_rows_on_tile(frame: np.ndarray, scale_percent: int, dest_w: int, dest_h: int) -> int:
    h, w = frame.shape[:2]
    if w < 1 or h < 1:
        return 0
    scale = max(max(int(dest_w), 1) / w, max(int(dest_h), 1) / h)
    crop = max(0, (round(h * scale) - max(int(dest_h), 1)) // 2)
    return max(0, math.ceil(BAR_H * min(scale_percent, 100) / 100 * scale) - crop)


def hud_lines_for_source(slot: CameraSlot, src: FrameSource) -> list[str]:
    """Build overlay lines from a live slot + frame source."""
    cam = getattr(slot, "camera", None)
    kind = getattr(cam, "kind", "") if cam is not None else ""
    tag = str(getattr(src, "device_tag", "") or kind or "cam")
    w = int(getattr(src, "actual_width", 0) or getattr(src, "width", 0) or 0)
    h = int(getattr(src, "actual_height", 0) or getattr(src, "height", 0) or 0)
    # Line 1 = operator-selected mode; line 2 = measured delivery (not file stamp).
    slot_mode = getattr(slot, "mode", None)
    mode_fps = int(
        getattr(src, "requested_fps", 0)
        or (getattr(slot_mode, "fps", 0) if slot_mode is not None else 0)
        or getattr(src, "target_fps", 0)
        or 0
    )
    stamped = int(getattr(src, "target_fps", 0) or 0)
    camera_fps = float(getattr(src, "actual_fps", 0) or 0)
    preview_fps = 0.0
    est = getattr(slot, "estimate_preview_fps", None)
    if callable(est):
        live = est()
        if live and live > 1:
            preview_fps = float(live)
    rec = ""
    try:
        if slot.pipeline and slot.pipeline.camera_handler.is_recording:
            rec = "REC"
    except AttributeError:
        rec = ""
    line1 = f"{tag}  {w}x{h}@{mode_fps}"
    if rec:
        line1 = f"{rec}  {line1}"
    lines = [line1, f"delivery ~{camera_fps:.0f} fps  (file stamp {stamped})"]
    if getattr(src, "_ffmpeg", None) is not None:
        lines.append("capture: ffmpeg DirectShow (OBS pin)")
        ff = getattr(src, "_ffmpeg", None)
        if ff is not None and getattr(ff, "pipe_pix_fmt", "") == PIPE_NV12:
            lines.append("pipe: NV12 (BGR deferred)")
    if preview_fps > 1:
        lines.append(f"UI paint ~{preview_fps:.0f} fps (not file FPS)")
    if mode_fps >= settings.high_rate_fps and camera_fps < mode_fps * 0.85 and tag == "elgato":
        lines.append(capture_not_120_hud_hint(camera_fps, mode_fps, stamped))
    elif kind == "realsense" and mode_fps >= settings.high_rate_fps:
        lines.append("D400 color is not 120fps")
    return lines
