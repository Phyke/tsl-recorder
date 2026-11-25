"""
Intel RealSense SDK recording (legacy .bag or ROS2 .db3).

Newer librealsense builds (common on Windows) require ``.db3``. Older builds
use ``.bag``. We default to ``.db3`` and fall back to ``.bag`` if needed.

Recording is armed only at Record time — never during Start preview.
"""

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from recorder.config import settings
from recorder.frame_source import FrameSource

logger = logging.getLogger("poc1.bag")


# Cached after first successful probe: ".bag" or ".db3"
# Default .db3 — modern pyrealsense2 rejects .bag with:
#   "Output file must have .db3 extension"
@dataclass
class _SuffixState:
    record_suffix: str | None = ".db3"


_state = _SuffixState()


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        msg = "git is not on PATH"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        msg = f"git {' '.join(args)} failed: {result.stderr.strip()}"
        raise RuntimeError(msg)
    return result.stdout.strip()


# Build stamp so operators can confirm which code runs: "sdk-record-v{number}-{commit date} {short hash}".
# The number is settings.poc1_last_build plus the commit count, so each commit adds 1.
# With no git (or no repository), the stamp is "unknown commit".
def _build_id() -> str:
    try:
        count = int(_git("rev-list", "--count", "HEAD"))
        commit_hash, commit_date = _git("log", "-1", "--date=short", "--format=%h %cd").split()
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError):
        return "unknown commit"
    return f"sdk-record-v{settings.poc1_last_build + count}-{commit_date} {commit_hash}"


BUILD_ID = _build_id()


def can_record_bag(source: FrameSource) -> bool:
    return getattr(source, "mode", None) == "hardware"


def recording_suffix() -> str:
    """Return the file suffix this pyrealsense2 build accepts for record_to_file."""
    if _state.record_suffix:
        return _state.record_suffix
    return ".db3"


def set_recording_suffix(suffix: str) -> None:
    suf = suffix if suffix.startswith(".") else f".{suffix}"
    if suf not in {".bag", ".db3"}:
        suf = ".db3"
    _state.record_suffix = suf
    logger.info("RealSense SDK record suffix set to %s", suf)


def coerce_record_path(path: Path) -> Path:
    """
    Force a path the current SDK will accept.

    Always rewrites to the preferred suffix (.db3 by default). Also strips
    legacy hidden ``.pending_*`` names if anything still passes one in.
    """
    path = Path(path)
    name = path.name
    if name.startswith(".pending_"):
        # Old builds used .pending_cam1_slot0.bag — never write those.
        clean = name[len(".pending_") :]
        if clean.endswith((".bag", ".db3")):
            stem = Path(clean).stem
        else:
            stem = clean
        path = path.with_name(stem + recording_suffix())
    return path.with_suffix(recording_suffix())


def with_sdk_record_suffix(path: Path) -> Path:
    """Rewrite path to the SDK-required suffix (.bag or .db3)."""
    return coerce_record_path(path)


def paths_to_try(path: Path) -> list[Path]:
    """Candidate record paths: preferred suffix first (.db3), then the other."""
    path = Path(path)
    primary = coerce_record_path(path)
    other_suf = ".bag" if primary.suffix.lower() == ".db3" else ".db3"
    secondary = primary.with_suffix(other_suf)
    out: list[Path] = [primary]
    if secondary != primary:
        out.append(secondary)
    return out


def _restore_wanted(source: FrameSource, wanted: tuple[int, int, int, str]) -> None:
    w, h, fps, fmt = wanted
    source.width = w
    source.height = h
    source.target_fps = fps
    source.pixel_format = fmt
    if hasattr(source, "wanted"):
        source.wanted = wanted


def _wanted_from_source(source: FrameSource) -> tuple[int, int, int, str]:
    wanted = (
        int(getattr(source, "width", 640)),
        int(getattr(source, "height", 480)),
        int(getattr(source, "target_fps", 30) or 30),
        str(getattr(source, "pixel_format", "bgr8") or "bgr8"),
    )
    saved_wanted = getattr(source, "wanted", None)
    if isinstance(saved_wanted, tuple) and len(saved_wanted) == 4:  # noqa: PLR2004
        wanted = (
            int(saved_wanted[0]),
            int(saved_wanted[1]),
            int(saved_wanted[2]),
            str(saved_wanted[3]),
        )
    return wanted


def _call_start(source: FrameSource, *, allow_fallback: bool) -> None:
    start = source.start
    try:
        start(allow_fallback=allow_fallback)
    except TypeError:
        start()


def pending_bag_path(out_dir: Path, slot_id: int, prefix: str) -> Path:
    """Unused (preview must not arm recording). Kept for API compatibility."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefix) or "cam"
    return Path(out_dir).resolve() / f"{safe}_slot{slot_id}_recording{recording_suffix()}"


def prearm_bag_on_source(source: FrameSource, bag_path: Path) -> None:
    """No-op: never arm SDK recording during preview (causes Start preview errors)."""
    clear_bag_on_source(source)
    _ = bag_path


def clear_bag_on_source(source: FrameSource) -> None:
    """Ensure preview starts without enable_record_to_file."""
    if hasattr(source, "bag_path"):
        source.bag_path = None
    if hasattr(source, "active_bag_path"):
        source.active_bag_path = None
    if hasattr(source, "bag_final_path"):
        source.bag_final_path = None
    if hasattr(source, "bag_start_paused"):
        source.bag_start_paused = False
    if hasattr(source, "rs_recorder"):
        source.rs_recorder = None


def _raise_no_record_file() -> NoReturn:
    msg = "SDK created no record file after start"
    raise RuntimeError(msg)


def start_bag_recording(source: FrameSource, bag_path: Path) -> Path:
    """
    Restart RealSense with SDK file recording to ``bag_path``.

    Tries ``.db3`` first (required by newer SDKs), then ``.bag``.
    Raises RuntimeError if arming fails.
    """
    if not can_record_bag(source):
        msg = (
            "RealSense recording requires a live [realsense] hardware device "
            "(not UVC twin / simulation)."
        )
        raise RuntimeError(msg)
    try:
        import pyrealsense2 as rs  # noqa: F401
    except ImportError as exc:
        msg = (
            "pyrealsense2 is not installed — cannot write RealSense bag.\n"
            "Run: uv sync --extra realsense"
        )
        raise RuntimeError(msg) from exc

    # Attach to the running preview streams when they are the record streams: no stop and no start at Record.
    attach = getattr(source, "attach_bag", None)
    if callable(attach):
        attached = coerce_record_path(Path(bag_path)).resolve()
        attached.parent.mkdir(parents=True, exist_ok=True)
        if attach(attached):
            if attached.is_file():
                attached.unlink()
            logger.info("RealSense record armed (attach, no restart) -> %s", attached)
            return attached

    wanted = _wanted_from_source(source)
    last_exc: BaseException | None = None
    candidates = paths_to_try(Path(bag_path))

    for raw_candidate in candidates:
        candidate = raw_candidate.resolve()
        candidate.parent.mkdir(parents=True, exist_ok=True)
        if candidate.exists():
            try:
                if candidate.is_file():
                    candidate.unlink()
            except OSError as exc:
                last_exc = exc
                continue

        for attempt in range(1, 4):
            try:
                try:
                    source.stop()
                except Exception:
                    logger.debug("Could not stop the source", exc_info=True)
                time.sleep(0.12 * attempt)

                source.bag_path = candidate
                source.active_bag_path = candidate
                source.bag_final_path = candidate
                # Write nothing until the MP4 recorders arm. enable_record_to_file
                # starts at pipeline.start(); pause runs immediately after that.
                if hasattr(source, "bag_start_paused"):
                    source.bag_start_paused = True
                _restore_wanted(source, wanted)
                _call_start(source, allow_fallback=False)

                paused = bool(
                    getattr(source, "bag_start_paused", False)
                    and getattr(source, "rs_recorder", None) is not None
                    and getattr(source, "_pipeline", None) is not None
                )
                if not paused:
                    size = 0
                    for _ in range(12):
                        try:
                            _ = source.read()
                        except Exception:
                            logger.debug("Could not read a frame from the source", exc_info=True)
                        if candidate.exists():
                            try:
                                size = candidate.stat().st_size
                            except OSError:
                                size = 0
                            if size > 0:
                                break
                        if candidate.with_suffix("").exists():
                            break
                        time.sleep(0.05)

                    if candidate.exists() and candidate.stat().st_size <= 0 and getattr(source, "_pipeline", None) is None:
                        _raise_no_record_file()

                set_recording_suffix(candidate.suffix)
                flush = getattr(source, "flush_to_live", None)
                if callable(flush):
                    try:
                        flush()
                    except Exception:
                        logger.debug("RealSense flush_to_live after bag arm failed", exc_info=True)
                logger.info(
                    "RealSense record armed (try %d) paused=%s -> %s (exists=%s size=%s)",
                    attempt,
                    paused,
                    candidate,
                    candidate.exists(),
                    candidate.stat().st_size if candidate.exists() else 0,
                )
                return candidate
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc).lower()
                logger.warning(
                    "Record arm failed for %s (try %d): %s",
                    candidate.name,
                    attempt,
                    exc,
                )
                try:
                    clear_bag_on_source(source)
                    if candidate.exists() and candidate.is_file() and candidate.stat().st_size == 0:
                        candidate.unlink(missing_ok=True)
                except Exception:
                    logger.debug("Could not clean up the empty bag file", exc_info=True)
                if "db3" in msg and candidate.suffix.lower() == ".bag":
                    set_recording_suffix(".db3")
                    break
                if (
                    "bag" in msg
                    and "extension" in msg
                    and candidate.suffix.lower() == ".db3"
                ):
                    set_recording_suffix(".bag")
                    break

    try:
        clear_bag_on_source(source)
        _restore_wanted(source, wanted)
        if getattr(source, "_pipeline", None) is None:
            time.sleep(0.3)
            try:
                _call_start(source, allow_fallback=False)
            except (RuntimeError, ValueError):
                _restore_wanted(source, wanted)
                _call_start(source, allow_fallback=True)
    except Exception:
        logger.exception("Could not restore RealSense preview after record failure")

    msg_0 = (
        f"Could not start RealSense SDK recording ({Path(bag_path).name}).\n"
        f"Last error: {last_exc}.\n"
        "This PC's RealSense SDK may require .db3 instead of .bag (or the reverse).\n"
        "Close Intel RealSense Viewer, use USB 3, then Record again."
    )
    raise RuntimeError(msg_0)


def stop_bag_recording(source: FrameSource) -> Path | None:
    """Finalize SDK recording by stopping the pipeline, then restore preview."""
    path = getattr(source, "active_bag_path", None) or getattr(source, "bag_path", None)
    final = getattr(source, "bag_final_path", None) or path
    if not path or not can_record_bag(source):
        clear_bag_on_source(source)
        return None

    wanted = _wanted_from_source(source)
    detached = False
    try:
        if hasattr(source, "pause_bag"):
            try:
                source.pause_bag()
            except Exception:
                logger.debug("Could not pause the bag", exc_info=True)
            # Let the SDK write the frames it already accepted before the
            # pipeline is torn down. Stopping immediately drops that tail,
            # so the MP4 (still draining its queue) ends up longer than the .db3.
            time.sleep(0.15)
        # Attach: the release of the recorder closes the .db3, and the preview streams stay on.
        detach = getattr(source, "detach_bag", None)
        detached = bool(callable(detach) and detach())
        if not detached:
            source.stop()
    except Exception:
        logger.debug("Could not detach or stop the bag source", exc_info=True)

    clear_bag_on_source(source)
    if not detached:
        time.sleep(0.4)
        _restore_wanted(source, wanted)
        try:
            _call_start(source, allow_fallback=False)
        except (RuntimeError, ValueError):
            try:
                _restore_wanted(source, wanted)
                _call_start(source, allow_fallback=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RealSense preview restart after record failed: %s", exc)

    out = Path(final) if final is not None else Path(path)
    if out.exists() and out.stat().st_size > 0:
        logger.info("RealSense record saved: %s (%d bytes)", out, out.stat().st_size)
        return out
    if out.suffix.lower() == ".db3":
        parent = out.parent
        stem = out.stem
        for child in parent.glob(f"{stem}*"):
            if child.is_file() and child.stat().st_size > 0:
                logger.info(
                    "RealSense record saved: %s (%d bytes)", child, child.stat().st_size
                )
                return child
    logger.error("No RealSense record file produced at %s", out)
    return None
