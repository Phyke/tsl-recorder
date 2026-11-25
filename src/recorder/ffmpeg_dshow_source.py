"""
ffmpeg DirectShow capture for Elgato @1080p120 (OBS-equivalent pin).

OpenCV CAP_DSHOW often soft-opens ~60 while OBS/ffmpeg lock 120 on the same HDMI.
This module spawns ``ffmpeg -f dshow`` and reads raw frames from stdout (NV12 at
≥90fps to halve pipe bandwidth; BGR24 otherwise).
"""

import contextlib
import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from recorder.config import recording_settings_for, settings
from recorder.frame_layout import (
    frame_bytes,
    pipe_pix_fmt_for_fps,
    reshape_raw_frame,
)

logger = logging.getLogger("poc1.ffmpeg_dshow")

# The line that ffmpeg (libavdevice/dshow.c) writes for each frame that the real-time buffer (-rtbufsize) drops.
# Group 1: the buffer fill in percent.
_RTBUF_DROP = re.compile(r"real-time buffer .* \((\d+)% of size: \d+ \[rtbufsize parameter\]\)! frame dropped!")

# Elgato MJPEG is an input *codec* on dshow (-vcodec mjpeg), not -pixel_format mjpeg.
InputMode = str  # "vcodec_mjpeg" | "pixel_yuyv422" | "none"


def _input_modes_to_try(requested_fmt: str) -> list[InputMode]:
    fmt = (requested_fmt or "mjpg").lower()
    modes: list[InputMode] = []
    if fmt in {"mjpg", "mjpeg"}:
        modes.append("vcodec_mjpeg")
    elif fmt in {"yuyv", "yuy2"}:
        modes.append("pixel_yuyv422")
    else:
        modes.append("none")
    for extra in ("pixel_yuyv422", "none"):
        if extra not in modes:
            modes.append(extra)
    return modes


def _pin_args_for_mode(mode: InputMode) -> list[str]:
    if mode == "vcodec_mjpeg":
        return ["-vcodec", "mjpeg"]
    if mode == "pixel_yuyv422":
        return ["-pixel_format", "yuyv422"]
    return []


def find_ffmpeg() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    if sys.platform != "win32":
        return None
    for path in (
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "ffmpeg.exe",
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
    ):
        if path.is_file():
            return str(path)
    return None


def dshow_input_names(open_path: str | None) -> list[str]:
    """Plain DirectShow device names for ffmpeg ``-i video=…``."""
    from recorder.win_names import elgato_open_name_paths

    names: list[str] = []

    def _add(raw: str) -> None:
        name = raw.strip()
        if name.startswith("video="):
            name = name[6:].strip()
        if name and name not in names:
            names.append(name)

    if open_path:
        _add(open_path)
    for path in elgato_open_name_paths():
        _add(path)
    return names


def _pixel_formats_to_try(requested_fmt: str) -> list[InputMode]:
    """Backward-compatible alias for tests."""
    return _input_modes_to_try(requested_fmt)


def _lock_threshold(requested_fps: int) -> float:
    """Minimum measured fps to treat high-rate (≥90 ask) as locked."""
    req = int(requested_fps) if int(requested_fps) > 0 else 30
    if req >= settings.high_rate_fps:
        return float(settings.high_rate_fps)
    return req * 0.85


class FfmpegDshowCaptureSource:
    """Read raw frames from ``ffmpeg -f dshow`` stdout (NV12 or BGR24)."""

    allow_fps_remux = True

    def __init__(
        self,
        width: int,
        height: int,
        fps: int,
        *,
        device_names: list[str],
        pixel_format: str = "mjpg",
        device_tag: str = "elgato",
        pipe_pix_fmt: str | None = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.target_fps = int(fps) if int(fps) > 0 else 30
        self.requested_fps = self.target_fps
        self.pixel_format = pixel_format
        self.device_tag = device_tag
        self.actual_fps = 0.0
        self.actual_width = self.width
        self.actual_height = self.height
        self._device_names = [n for n in device_names if n]
        self._ffmpeg: str | None = find_ffmpeg()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.pipe_pix_fmt = pipe_pix_fmt or pipe_pix_fmt_for_fps(self.target_fps)
        self._frame_bytes = frame_bytes(self.width, self.height, self.pipe_pix_fmt)
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=recording_settings_for(device_tag).ffmpeg_queue_frames)
        self.queue_limit = self._queue.maxsize
        # The frames that the real-time buffer dropped since the lock, and the buffer fill (%) at the last drop.
        self.rtbuf_drops = 0
        self.rtbuf_last_percent = 0
        self._pending: np.ndarray | None = None
        self._active_device = ""
        self._active_input_mode = ""

    def start(self) -> None:
        if sys.platform != "win32":
            msg = "ffmpeg DirectShow capture is Windows-only"
            raise RuntimeError(msg)
        if not self._ffmpeg:
            msg = (
                "ffmpeg not found on PATH — install ffmpeg so Elgato can open at 1080p120 "
                "(same pin OBS uses)."
            )
            raise RuntimeError(msg)
        if not self._device_names:
            msg = "No DirectShow device name for ffmpeg Elgato capture"
            raise RuntimeError(msg)

        want = self.target_fps
        threshold = _lock_threshold(want)
        last_err = "no attempt"
        for device in self._device_names:
            for mode in _input_modes_to_try(self.pixel_format):
                for measure_s in (3.5, 5.0, 7.0):
                    try:
                        rate = self._try_open(device, mode, measure_s=measure_s)
                    except Exception as exc:  # noqa: BLE001
                        last_err = str(exc)
                        logger.warning(
                            "ffmpeg dshow open failed %s %dx%d@%d mode=%s: %s",
                            device,
                            self.width,
                            self.height,
                            want,
                            mode,
                            exc,
                        )
                        continue
                    if rate >= threshold:
                        self.actual_fps = rate
                        self._active_device = device
                        self._active_input_mode = mode
                        logger.info(
                            "ffmpeg dshow LOCKED %s %dx%d@%d mode=%s pipe=%s measured ~%.1ffps",
                            device,
                            self.width,
                            self.height,
                            want,
                            mode,
                            self.pipe_pix_fmt,
                            rate,
                        )
                        self.rtbuf_drops = 0
                        self._seek_live_edge()
                        return
                    self.stop()
                    last_err = f"{device} mode={mode} measured ~{rate:.1f}fps"
                    logger.warning(
                        "ffmpeg dshow %s %dx%d@%d mode=%s only ~%.1ffps (need %.0f) — retrying",
                        device,
                        self.width,
                        self.height,
                        want,
                        mode,
                        rate,
                        threshold,
                    )

        msg = (
            f"ffmpeg could not lock Elgato at {self.width}x{self.height}@{want} "
            f"(last: {last_err}).\nConfirm HDMI is 1080p120 and OBS is fully exited."
        )
        raise RuntimeError(msg)

    def _build_cmd(self, device: str, input_mode: InputMode) -> list[str]:
        """OBS-style dshow pin — framerate before size, vcodec mjpeg for Elgato."""
        return self._build_cmd_variant(device, input_mode, fps_first=True)

    def _build_cmd_variant(
        self, device: str, input_mode: InputMode, *, fps_first: bool
    ) -> list[str]:
        if not self._ffmpeg:
            msg = "ffmpeg not found on PATH"
            raise RuntimeError(msg)
        head = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            # "repeat" stops the merge of identical lines into "Last message repeated N times", so each drop has a line.
            "repeat+warning",
            "-nostdin",
            "-fflags",
            "nobuffer",
            "-f",
            "dshow",
            "-rtbufsize",
            recording_settings_for(self.device_tag).dshow_rtbufsize,
        ]
        size_args = ["-video_size", f"{self.width}x{self.height}"]
        fps_args = ["-framerate", str(self.target_fps)]
        fmt_args = _pin_args_for_mode(input_mode)
        if fps_first:
            pin = fps_args + size_args + fmt_args
        else:
            pin = size_args + fps_args + fmt_args
        return head + pin + [
            "-i",
            f"video={device}",
            "-an",
            "-pix_fmt",
            self.pipe_pix_fmt,
            "-f",
            "rawvideo",
            "pipe:1",
        ]

    def _cmd_variants(self, device: str, input_mode: InputMode) -> list[list[str]]:
        return [
            self._build_cmd_variant(device, input_mode, fps_first=True),
            self._build_cmd_variant(device, input_mode, fps_first=False),
        ]

    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self._frame_bytes * 2,
            creationflags=creationflags,
        )

    def _try_open(self, device: str, input_mode: InputMode, measure_s: float) -> float:
        last_rate = 0.0
        for cmd in self._cmd_variants(device, input_mode):
            rate = self._try_open_cmd(cmd, measure_s)
            last_rate = max(last_rate, rate)
            if rate >= _lock_threshold(self.target_fps):
                return rate
            self.stop()
        return last_rate

    def _try_open_cmd(
        self, cmd: list[str], measure_s: float
    ) -> float:
        logger.info("ffmpeg dshow try: %s", " ".join(cmd[1:18]))
        proc = self._spawn(cmd)
        self._proc = proc
        self._running = True
        self._thread = threading.Thread(
            target=self._reader_loop, name="ffmpeg-dshow-read", daemon=True
        )
        self._thread.start()
        threading.Thread(
            target=self._drain_stderr, args=(proc,), name="ffmpeg-dshow-err", daemon=True
        ).start()

        # Warmup — DirectShow can deliver a burst then settle at true rate.
        warmup_end = time.perf_counter() + 0.6
        while time.perf_counter() < warmup_end:
            try:
                frame = self._queue.get(timeout=0.15)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if frame is None:
                break

        n = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < measure_s:
            try:
                frame = self._queue.get(timeout=0.25)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if frame is None:
                break
            n += 1
        elapsed = max(time.perf_counter() - t0, 0.05)
        rate = (n - 1) / elapsed if n >= 2 else (n / elapsed if n >= 1 and elapsed > settings.single_frame_rate_min_s else 0.0)  # noqa: PLR2004
        if proc.poll() is not None and n == 0:
            err = ""
            with contextlib.suppress(OSError, ValueError):
                err = (proc.stderr.read() or b"").decode("utf-8", errors="replace")[:400]
            raise RuntimeError(err or "ffmpeg exited with no frames")
        return rate

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.debug("ffmpeg: %s", text)
                drop = _RTBUF_DROP.search(text)
                if drop is not None:
                    self.rtbuf_drops += 1
                    self.rtbuf_last_percent = int(drop.group(1))
        except (OSError, ValueError):
            logger.warning("The ffmpeg stderr reader stopped", exc_info=True)

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        stdout = proc.stdout
        nbytes = self._frame_bytes
        try:
            while self._running:
                buf = stdout.read(nbytes)
                if len(buf) != nbytes:
                    break
                frame = reshape_raw_frame(
                    buf, self.width, self.height, self.pipe_pix_fmt
                )
                owned = np.ascontiguousarray(frame)
                try:
                    self._queue.put_nowait(owned)
                except queue.Full:
                    with contextlib.suppress(queue.Empty):
                        self._queue.get_nowait()
                    with contextlib.suppress(queue.Full):
                        self._queue.put_nowait(owned)
        finally:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)

    def _seek_live_edge(self) -> None:
        """Discard buffered frames from lock/measure; keep only the newest."""
        self._pending = None
        latest: np.ndarray | None = None
        while True:
            try:
                frame = self._queue.get_nowait()
            except queue.Empty:
                break
            if frame is None:
                continue
            latest = frame
        self._pending = latest

    def _dequeue_newest(self, *, block: bool, timeout: float = 2.0) -> np.ndarray | None:
        """Return the freshest queued frame (drop older buffered copies)."""
        if block:
            try:
                frame = self._queue.get(timeout=timeout)
            except queue.Empty:
                return None
        else:
            try:
                frame = self._queue.get_nowait()
            except queue.Empty:
                return None
        if frame is None:
            return None
        while True:
            try:
                nxt = self._queue.get_nowait()
            except queue.Empty:
                break
            if nxt is None:
                return None
            frame = nxt
        return frame

    # The frames in the queue between the ffmpeg reader and the capture thread, for the Record tab.
    def queued(self) -> int:
        return self._queue.qsize()

    def read(self) -> np.ndarray | None:
        if not self._running:
            return None
        if self._pending is not None:
            frame = self._pending
            self._pending = None
            newer = self._dequeue_newest(block=False)
            return newer if newer is not None else frame
        return self._dequeue_newest(block=True)

    def flush_to_live(self) -> None:
        """Drop queued pipe frames so Record starts at the live edge."""
        self._pending = None
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                break

    def stop(self) -> None:
        self._running = False
        proc = self._proc
        self._proc = None
        if proc is not None:
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:  # noqa: BLE001
                with contextlib.suppress(OSError):
                    proc.kill()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
