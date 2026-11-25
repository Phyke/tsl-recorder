"""
Processor: compression stage between camera_handler and recorder.

Spec shape:
    camera_handler → processor (compression) → recorder

This stage owns the VideoWriter encode (H.264 when available, else mp4v).
Raw ~0.75GB/s frames are compressed here; the recorder only accounts
encoded units and may remux FPS metadata after stop.
"""

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2

from recorder.camera_handler import DropCountingQueue, FrameEnvelope
from recorder.codec import choose_best_fourcc
from recorder.frame_layout import ensure_bgr
from recorder.frame_source import embed_seq_barcode

logger = logging.getLogger("poc1.processor")


@dataclass
class EncodedEnvelope:
    """Lightweight token after compression — recorder does not re-encode."""
    seq: int
    capture_ts: float
    encoded_bytes_est: int = 0


@dataclass
class Processor:
    in_queue: DropCountingQueue
    out_queue_size: int = 512
    # The MP4 codec ("auto" or "mp4v") and the longest drain at Stop (s): the pipeline sets them at each Record.
    codec: str = "auto"
    drain_s: float = 60.0

    def __post_init__(self) -> None:
        self.out_queue: DropCountingQueue[EncodedEnvelope] = DropCountingQueue(
            self.out_queue_size, drop_oldest=False, name="recorder_in"
        )
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._encode_lock = threading.Lock()
        self.frames_processed = 0
        self.bytes_encoded_est = 0
        self._writer: cv2.VideoWriter | None = None
        self.output_path: Path | None = None
        self.width = 0
        self.height = 0
        self.fps = 30
        self.chosen_fourcc = "mp4v"
        self.codec_label = "MPEG-4 (mp4v)"
        self._slow_encodes = 0
        self._slow_encode_ms_max = 0.0
        self.sidecar_bag = None
        # The frames still in the queue when the drain limit at Stop ends. Stop deletes them, so they are not in the MP4.
        self.dropped_at_stop = 0
        # The end of the drain at Stop (time.perf_counter), while the drain runs. The Record tab shows the time left.
        self.drain_deadline: float | None = None
        # (barcode seq, camera frame number) of each frame in the MP4, in the MP4 order, for the frames CSV of the take.
        self.frame_log: list[tuple[int, int | None]] = []

    def configure_output(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        fourcc: str | None = None,
    ) -> None:
        self.output_path = output_path
        self.width = width
        self.height = height
        self.fps = fps
        if fourcc:
            self.chosen_fourcc = fourcc
            self.codec_label = fourcc
        elif self.codec == "mp4v":
            self.chosen_fourcc, self.codec_label = "mp4v", "MPEG-4 (mp4v)"
        else:
            self.chosen_fourcc, self.codec_label = choose_best_fourcc()

    def start(self) -> None:
        if self.output_path is None:
            msg = "Processor.configure_output() required before start()"
            raise RuntimeError(msg)
        if self._thread is not None and self._thread.is_alive():
            self.stop()

        self.frames_processed = 0
        self.bytes_encoded_est = 0
        self._slow_encodes = 0
        self._slow_encode_ms_max = 0.0
        self.out_queue.reset_dropped()
        self.dropped_at_stop = 0
        self.frame_log = []
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        fourcc = cv2.VideoWriter_fourcc(*self.chosen_fourcc)
        self._writer = cv2.VideoWriter(
            str(self.output_path), fourcc, float(self.fps), (self.width, self.height)
        )
        if not self._writer.isOpened() and self.chosen_fourcc != "mp4v":
            self.chosen_fourcc, self.codec_label = "mp4v", "MPEG-4 (mp4v)"
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(
                str(self.output_path), fourcc, float(self.fps), (self.width, self.height)
            )
        if not self._writer.isOpened():
            msg = (
                f"Processor VideoWriter failed for {self.output_path} "
                f"fourcc={self.chosen_fourcc}"
            )
            raise RuntimeError(msg)
        logger.info(
            "Processor encoding -> %s (%dx%d@%d) codec=%s",
            self.output_path, self.width, self.height, self.fps, self.codec_label,
        )
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="processor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """
        Stop accepting new work, then encode every frame still in the queue.

        Discarding the backlog made Stop feel instant but failed the no-drop
        proof (e.g. 400 written of 477 read). Drain is the correct Stop.
        """
        self._running.clear()
        if self._thread:
            self._thread.join(timeout=8.0)
            if self._thread.is_alive():
                logger.warning("processor thread still encoding; continuing drain")
            self._thread = None
        drained = 0
        deadline = time.perf_counter() + self.drain_s
        self.drain_deadline = deadline
        while time.perf_counter() < deadline:
            env = self.in_queue.get(timeout=0.05)
            if env is None:
                if self.in_queue.qsize() == 0:
                    break
                continue
            self._encode(env)
            drained += 1
        self.drain_deadline = None
        leftover = self.in_queue.discard_all()
        self.dropped_at_stop = leftover
        if drained:
            logger.info("processor stop: encoded %d queued frame(s)", drained)
        if leftover:
            logger.error(
                "processor stop: %d frame(s) still pending after drain timeout",
                leftover,
            )
        with self._encode_lock:
            if self._writer is not None:
                try:
                    self._writer.release()
                except Exception:
                    logger.exception("VideoWriter.release failed")
                self._writer = None
        if self._slow_encodes:
            logger.info(
                "processor: %d/%d encodes exceeded realtime budget (max %.1fms)",
                self._slow_encodes, self.frames_processed, self._slow_encode_ms_max,
            )

    # The whole seconds left of the drain limit at Stop, or None when no drain runs.
    def drain_seconds_left(self) -> int | None:
        deadline = self.drain_deadline
        if deadline is None:
            return None
        return math.ceil(max(0.0, deadline - time.perf_counter()))

    def _encode(self, env: FrameEnvelope) -> None:
        with self._encode_lock:
            if self._writer is None:
                return
            frame = ensure_bgr(env.frame)
            h, w = frame.shape[:2]
            if w != self.width or h != self.height:
                frame = cv2.resize(frame, (self.width, self.height))

            if env.seq >= 0:
                embed_seq_barcode(frame, env.seq)

            t0 = time.perf_counter()
            self._writer.write(frame)
            self.frame_log.append((env.seq, env.frame_number))
            encode_ms = (time.perf_counter() - t0) * 1000.0
            sidecar = getattr(self, "sidecar_bag", None)
            if sidecar is not None:
                try:
                    sidecar.submit(frame, env.capture_ts)
                except Exception:
                    logger.exception("sidecar bag submit failed")
            budget = (1000.0 / max(self.fps, 1)) * 2
            if encode_ms > budget:
                self._slow_encodes += 1
                self._slow_encode_ms_max = max(self._slow_encode_ms_max, encode_ms)

            raw = self.width * self.height * 3
            est = max(1, raw // 40)
            self.bytes_encoded_est += est
            self.frames_processed += 1
            self.out_queue.put(
                EncodedEnvelope(
                    seq=env.seq, capture_ts=env.capture_ts, encoded_bytes_est=est
                )
            )

    def _drain(self) -> None:
        while True:
            env = self.in_queue.get(timeout=0.05)
            if env is None:
                break
            self._encode(env)

    def _loop(self) -> None:
        while self._running.is_set():
            env: FrameEnvelope | None = self.in_queue.get(timeout=0.1)
            if env is None:
                continue
            self._encode(env)
        # Do not drain the backlog here — stop() discards pending frames so
        # Stop stays responsive. Only frames encoded above are kept.

    def summary(self) -> dict:
        return {
            "frames_processed": self.frames_processed,
            "bytes_encoded_est": self.bytes_encoded_est,
            "codec": self.codec_label,
            "fourcc": self.chosen_fourcc,
            "output_path": str(self.output_path) if self.output_path else "",
            "slow_encodes": self._slow_encodes,
            "compression_stage": "processor",
        }
