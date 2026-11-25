"""ROS2 color bag for Elgato/UVC (not Intel RealSense SDK .bag)."""

import contextlib
import logging
import queue
import shutil
import threading
import time
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger("poc1.uvc_rosbag")


class UvcRos2Bag:
    """
    Write JPEG CompressedImage messages to a ROS2 sqlite bag folder.

    SQLite must stay on one thread: the worker opens the Writer. JPEG can be
    encoded on the processor thread so the bag queue stays small.
    """

    def __init__(self, path: Path, *, queue_frames: int = 512, jpeg_quality: int = 80, drain_s: float = 25.0) -> None:
        self.path = Path(path)
        # The default 512 is ~4s at 120fps — overflow still increments ``dropped`` (honest report).
        self.queue_limit = queue_frames
        self.jpeg_quality = jpeg_quality
        self.drain_s = drain_s
        self._q: queue.Queue[tuple[bytes, int] | None] = queue.Queue(maxsize=queue_frames)
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self.frames_written = 0
        self.dropped = 0

    def start(self) -> Path:
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, name="uvc-rosbag", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=8.0):
            raise RuntimeError(self._error or "ROS2 bag worker did not start")
        if self._error:
            raise RuntimeError(self._error)
        logger.info("Elgato/UVC ROS2 bag -> %s", self.path)
        return self.path

    # The JPEG frames that wait for the .db3 write (the Record status line reads it).
    def queued(self) -> int:
        return self._q.qsize()

    def submit(self, bgr: np.ndarray, capture_ts: float) -> None:
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return
        ns = int(capture_ts * 1e9)
        try:
            self._q.put_nowait((bytes(buf), ns))
        except queue.Full:
            self.dropped += 1

    def stop(self) -> Path | None:
        # Let the worker finish queued JPEGs before sentinel (no software loss
        # at Stop when encode can keep up).
        deadline = time.monotonic() + self.drain_s
        while not self._q.empty() and time.monotonic() < deadline:
            time.sleep(0.02)
        try:
            self._q.put(None, timeout=5.0)
        except queue.Full:
            logger.warning("ROS2 bag queue full at stop — forcing sentinel")
            with contextlib.suppress(queue.Empty):
                self._q.get_nowait()
            self._q.put_nowait(None)
        if self._thread:
            self._thread.join(timeout=45.0)
            self._thread = None
        db3 = None
        if self.path.is_dir():
            files = list(self.path.glob("*.db3"))
            if files:
                db3 = files[0]
        logger.info(
            "UVC ROS2 bag stop: frames=%d dropped=%d file=%s",
            self.frames_written,
            self.dropped,
            db3,
        )
        return db3

    def _loop(self) -> None:
        writer = None
        try:
            from rosbags.rosbag2 import Writer
            from rosbags.typesys import Stores, get_typestore

            typestore = get_typestore(Stores.LATEST)
            CompressedImage = typestore.types["sensor_msgs/msg/CompressedImage"]  # noqa: N806
            Header = typestore.types["std_msgs/msg/Header"]  # noqa: N806
            Time = typestore.types["builtin_interfaces/msg/Time"]  # noqa: N806
            writer = Writer(self.path, version=Writer.VERSION_LATEST)
            writer.open()
            conn = writer.add_connection(
                "/camera/color/image_raw/compressed",
                CompressedImage.__msgtype__,
                typestore=typestore,
            )
            self._ready.set()
            while True:
                item = self._q.get()
                if item is None:
                    break
                jpeg, ns = item
                sec = ns // 1_000_000_000
                nsec = ns % 1_000_000_000
                msg = CompressedImage(
                    header=Header(
                        stamp=Time(sec=int(sec), nanosec=int(nsec)),
                        frame_id="elgato",
                    ),
                    format="jpeg",
                    data=np.frombuffer(jpeg, dtype=np.uint8),
                )
                cdr = typestore.serialize_cdr(msg, CompressedImage.__msgtype__)
                writer.write(conn, ns, cdr)
                self.frames_written += 1
        except Exception as exc:
            self._error = str(exc)
            logger.exception("ROS2 bag worker failed")
            self._ready.set()
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    logger.debug("Could not close the bag writer", exc_info=True)
