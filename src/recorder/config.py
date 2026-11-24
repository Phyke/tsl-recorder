# The settings of the app, in one object: `settings`. Each module reads `settings.<name>`.
# Fields: the settings of this PC. They are read from settings.json and saved when they change.
#   settings.json is not in git: each station has its own file.
# ClassVar values: fixed values, in git, the same on both stations. settings.json cannot change them
#   (a ClassVar key in the file is an unknown key, and the file is refused).
import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    settings_path: ClassVar[Path] = Path(__file__).resolve().parents[2] / "settings.json"
    default_save_folder: ClassVar[Path] = Path("./recordings/deliverable1")
    stations: ClassVar[tuple[str, ...]] = ("Station A", "Station B")
    camera_slot_range: ClassVar[tuple[int, int]] = (2, 8)
    # The font sizes of the code are written for 9 pt; the Font size setting scales them.
    base_font_size: ClassVar[int] = 9
    font_size_range: ClassVar[tuple[int, int]] = (8, 24)
    # DirectShow input buffer (ffmpeg -rtbufsize) — 150M caused ~1–3s preview lag vs RealSense.
    dshow_rtbufsize: ClassVar[str] = "20M"
    # Preview copies stay small so the UI stays responsive at 1080p/120 capture.
    preview_max_width: ClassVar[int] = 960
    preview_hz: ClassVar[float] = 15.0
    # The file prefixes of camera slots 1 and 2 before a camera is picked (m = mirrorless/Elgato, r = RealSense).
    default_prefixes: ClassVar[tuple[str, ...]] = ("m", "r")
    # Sensible presets when the driver does not expose a full mode list.
    uvc_preset_modes: ClassVar[tuple[tuple[int, int, int, str], ...]] = (
        (1280, 720, 30, "mjpg"),
        (640, 480, 30, "mjpg"),
        (1280, 720, 30, "yuyv"),
        (640, 480, 30, "yuyv"),
        (1280, 720, 30, "bgr8"),
        (640, 480, 30, "bgr8"),
        (1920, 1080, 30, "mjpg"),
        (1920, 1080, 30, "bgr8"),
        (1280, 720, 60, "mjpg"),
        (1920, 1080, 60, "mjpg"),
        (1920, 1080, 120, "mjpg"),
    )
    # The last build number of poc1 (realsense-recorder 93df15b, "sdk-record-v44-2026-10-12"). The build stamp continues from it.
    poc1_last_build: ClassVar[int] = 44

    font_size: int = Field(16, ge=font_size_range[0], le=font_size_range[1])
    save_folder: str = ""
    camera_slots: int = Field(2, ge=camera_slot_range[0], le=camera_slot_range[1])
    station: Literal[("",) + stations] = ""


settings = Settings()


# Reads the file into `settings` (the same object that every module holds). With no file, the defaults stay.
# A bad file raises pydantic.ValidationError, and `settings` does not change.
def load_settings(path: Path) -> None:
    if not path.exists():
        return
    loaded = Settings.model_validate_json(path.read_text(encoding="utf-8"))
    for name in Settings.model_fields:
        setattr(settings, name, getattr(loaded, name))


def save_settings(path: Path) -> None:
    path.write_text(json.dumps(settings.model_dump(), indent=2), encoding="utf-8")
