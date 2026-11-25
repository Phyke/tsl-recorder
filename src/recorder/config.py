# The settings of the app, in one object: `settings`. Each module reads `settings.<name>`.
# Fields: the settings of this PC. They are read from settings.json and saved when they change.
#   settings.json is not in git: each station has its own file.
# ClassVar values: fixed values, in git, the same on both stations. settings.json cannot change them
#   (a ClassVar key in the file is an unknown key, and the file is refused).
import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


# The preview settings of one camera type (Elgato, RealSense, or Other). The Preview page of the Settings tab sets them.
class PreviewSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fps_choices: ClassVar[tuple[int, ...]] = (5, 10, 15, 30, 60)
    # The size of the preview copy, in percent of the camera frame (x1, x0.75, x0.5, x0.25).
    scale_choices: ClassVar[tuple[int, ...]] = (100, 75, 50, 25)
    # The height of one HUD text line in pixels. At 18 px the font scale is 0.48.
    font_px_choices: ClassVar[tuple[int, ...]] = (12, 14, 16, 18, 20, 24, 28, 32)
    hud_base_font_px: ClassVar[int] = 18
    # The HUD text colors in RGB (the tile is RGB when the HUD is drawn).
    hud_colors: ClassVar[dict[str, tuple[int, int, int]]] = {
        "green": (40, 255, 90),
        "white": (255, 255, 255),
        "yellow": (255, 230, 0),
        "cyan": (0, 230, 255),
        "red": (255, 60, 60),
        "black": (0, 0, 0),
    }

    fps: Literal[fps_choices] = 15
    scale_percent: Literal[scale_choices] = 100
    font_px: Literal[font_px_choices] = 18
    color: Literal[tuple(hud_colors)] = "green"


# The recording settings of one camera type (RealSense, Elgato, or Other). The Recording page of the Settings tab sets them.
# The defaults are the values that the code used before the Recording page.
class RecordingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    queue_choices: ClassVar[tuple[int, ...]] = (128, 256, 512, 1024, 2048, 4800, 9600)
    deep_fps_choices: ClassVar[tuple[int, ...]] = (30, 50, 60, 90, 120)
    buffer_choices: ClassVar[tuple[int, ...]] = (1, 2, 4, 8, 16)
    rtbufsize_choices: ClassVar[tuple[str, ...]] = ("10M", "20M", "50M", "100M", "150M")
    ffmpeg_queue_choices: ClassVar[tuple[int, ...]] = (2, 4, 8, 16, 32)
    jpeg_quality_choices: ClassVar[tuple[int, ...]] = (50, 60, 70, 80, 90, 95)
    drain_s_choices: ClassVar[tuple[int, ...]] = (10, 25, 60, 120, 300)
    codec_choices: ClassVar[tuple[str, ...]] = ("auto", "mp4v")
    monitor_ms_choices: ClassVar[tuple[int, ...]] = (500, 1000, 2000, 5000)

    # The MP4 queue (frames between the capture and the encoder). The deep queue applies at deep_buffer_fps or more,
    # and to the fake camera.
    mp4_queue_frames: Literal[queue_choices] = 512
    mp4_queue_frames_deep: Literal[queue_choices] = 4800
    deep_buffer_fps: Literal[deep_fps_choices] = 60
    # The driver buffer of an OpenCV camera (CAP_PROP_BUFFERSIZE). The deep buffer applies at deep_buffer_fps or more.
    driver_buffer_frames: Literal[buffer_choices] = 1
    driver_buffer_frames_deep: Literal[buffer_choices] = 4
    # Elgato only: the driver buffer of the OBS-pin open (at settings.high_rate_fps or more).
    obs_pin_buffer_frames: Literal[buffer_choices] = 8
    # Elgato only: the ffmpeg DirectShow input buffer (-rtbufsize; 150M caused ~1-3s preview lag vs RealSense)
    # and the frame queue between ffmpeg and Python.
    dshow_rtbufsize: Literal[rtbufsize_choices] = "20M"
    ffmpeg_queue_frames: Literal[ffmpeg_queue_choices] = 8
    # Elgato only: the .db3 JPEG queue, the JPEG quality, and the longest .db3 drain at Stop (s).
    db3_queue_frames: Literal[queue_choices] = 512
    db3_jpeg_quality: Literal[jpeg_quality_choices] = 80
    db3_drain_s: Literal[drain_s_choices] = 25
    # The longest MP4 drain at Stop (s). The frames still in the queue after it are lost.
    mp4_drain_s: Literal[drain_s_choices] = 60
    # auto: H.264 when the OpenH264 DLL is present, else mp4v.
    mp4_codec: Literal[codec_choices] = "auto"
    # The interval of the CPU, RAM and disk samples in the monitor CSV of each take (ms).
    monitor_interval_ms: Literal[monitor_ms_choices] = 1000


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    settings_path: ClassVar[Path] = Path(__file__).resolve().parents[2] / "settings.json"
    default_save_folder: ClassVar[Path] = Path("./recordings/deliverable1")
    stations: ClassVar[tuple[str, ...]] = ("Station A", "Station B")
    camera_slot_range: ClassVar[tuple[int, int]] = (2, 8)
    # The font sizes of the code are written for 9 pt; the Font size setting scales them.
    base_font_size: ClassVar[int] = 9
    font_size_range: ClassVar[tuple[int, int]] = (8, 24)
    # A mode at this fps or more counts as high rate (Elgato 1080p120).
    high_rate_fps: ClassVar[int] = 90
    # A measured frame rate below this is not a real measurement (the camera is not live yet).
    min_live_fps: ClassVar[int] = 5
    # A file smaller than this counts as empty (the write failed).
    min_file_bytes: ClassVar[int] = 32
    # The status labels, and the HUD on a preview tile with no new frame, refresh at this interval (ms).
    # A preview tile also redraws at each new frame, at the Preview fps setting.
    ui_tick_ms: ClassVar[int] = 80
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
    # Frame sizes: full HD, HD and VGA.
    fhd_width: ClassVar[int] = 1920
    fhd_height: ClassVar[int] = 1080
    hd_width: ClassVar[int] = 1280
    hd_height: ClassVar[int] = 720
    vga_width: ClassVar[int] = 640
    vga_height: ClassVar[int] = 480
    # The largest depth size of a D400 camera.
    rs_depth_max_width: ClassVar[int] = 1280
    rs_depth_max_height: ClassVar[int] = 720
    # The most 1080p high-rate cameras in one take.
    max_fhd_high_rate_cameras: ClassVar[int] = 1
    # The fps of the Elgato high-rate mode.
    elgato_high_fps: ClassVar[int] = 120
    # The measured-fps bands that honest_container_fps snaps to 120, 60, 30 and 24.
    stamp_120_min_fps: ClassVar[float] = 100.0
    stamp_120_max_fps: ClassVar[float] = 130.0
    stamp_60_min_fps: ClassVar[float] = 48.0
    stamp_60_max_fps: ClassVar[float] = 63.0
    stamp_30_min_fps: ClassVar[float] = 28.0
    stamp_24_min_fps: ClassVar[float] = 20.0
    # An Elgato open that measures less than this does not climb to the high rate.
    no_climb_fps: ClassVar[float] = 45.0
    # An Elgato take that measures this fps or more counts as a 120 fps take (r7_120_ok).
    elgato_120_min_fps: ClassVar[float] = 110.0
    # A driver fps at or below this is unknown (the driver reports 0).
    driver_fps_unknown_max: ClassVar[float] = 0.5
    # A ROS2 export stamps the measured fps when it is more than this from 30.
    fps_restamp_tolerance: ClassVar[float] = 0.5
    # A measured fps within this fraction and within this many fps of the target is not a mismatch.
    fps_mismatch_rel: ClassVar[float] = 0.08
    fps_mismatch_abs: ClassVar[float] = 3.5
    # The live delivery fps of camera_handler: the samples kept, the fewest samples, and the shortest window (s).
    live_fps_history: ClassVar[int] = 180
    live_fps_min_samples: ClassVar[int] = 15
    live_fps_min_window_s: ClassVar[float] = 0.1
    # The preview fps of session: the samples kept and the fewest samples.
    preview_fps_history: ClassVar[int] = 90
    preview_fps_min_samples: ClassVar[int] = 8
    # A rate from a window shorter than this (s) is not a measurement.
    min_rate_window_s: ClassVar[float] = 0.05
    # A rate from one frame needs a window of this length (s) or more.
    single_frame_rate_min_s: ClassVar[float] = 0.2
    # A frame with a mean pixel value below this is black.
    black_frame_mean: ClassVar[float] = 1.5
    # Below this mean pixel value, the log warns that the UVC preview is nearly black.
    dark_frame_warn_mean: ClassVar[float] = 2.0
    # _looks_like_solid_green: the highest spread, the lowest green mean, and the highest red and blue means.
    solid_green_max_std: ClassVar[float] = 12.0
    solid_green_min_g: ClassVar[float] = 140.0
    solid_green_max_rb: ClassVar[float] = 70.0
    # _frame_is_unusable_elgato: the highest spread and the lowest green mean of a flat green frame.
    unusable_green_max_std: ClassVar[float] = 8.0
    unusable_green_min_g: ClassVar[float] = 120.0
    # _looks_like_packed_yuyv: the smallest frame, the fewest columns, and the stripe difference.
    yuyv_check_min_width: ClassVar[int] = 16
    yuyv_check_min_height: ClassVar[int] = 8
    yuyv_check_min_cols: ClassVar[int] = 4
    yuyv_stripe_delta: ClassVar[float] = 48.0
    # A barcode bit is 1 when its block mean is more than this.
    barcode_bit_threshold: ClassVar[float] = 127.0
    # A camera has a barcode when this many reads or more are valid.
    min_barcode_reads: ClassVar[int] = 2
    # A camera found only by its Windows name gets an index from this base.
    synthetic_index_base: ClassVar[int] = 100
    # After the bag recorder release, this many RealSense framesets must arrive.
    rs_release_check_framesets: ClassVar[int] = 3
    # A bag file smaller than this (bytes) is empty.
    min_bag_bytes: ClassVar[int] = 64
    # A bag export ends after this many frame timeouts in a row.
    playback_idle_limit: ClassVar[int] = 4
    # A preview tile smaller than this (pixels) is not drawn.
    min_tile_px: ClassVar[int] = 40
    # The most file names that the review popup lists.
    max_listed_files: ClassVar[int] = 6

    font_size: int = Field(16, ge=font_size_range[0], le=font_size_range[1])
    save_folder: str = ""
    camera_slots: int = Field(2, ge=camera_slot_range[0], le=camera_slot_range[1])
    station: Literal[("", *stations)] = ""
    # The defaults keep the old 960 px preview width for 1920 (Elgato) and 1280 (RealSense) frames.
    preview_elgato: PreviewSettings = Field(default_factory=lambda: PreviewSettings(scale_percent=50))
    preview_realsense: PreviewSettings = Field(default_factory=lambda: PreviewSettings(scale_percent=75))
    preview_other: PreviewSettings = Field(default_factory=PreviewSettings)
    recording_realsense: RecordingSettings = Field(default_factory=RecordingSettings)
    recording_elgato: RecordingSettings = Field(default_factory=RecordingSettings)
    recording_other: RecordingSettings = Field(default_factory=RecordingSettings)


settings = Settings()


# The recording settings of a camera type, from the device tag of the camera or of its frame source.
# Other: a webcam, the OBS virtual camera, a RealSense opened as UVC, or the fake camera.
def recording_settings_for(device_tag: str) -> RecordingSettings:
    if device_tag == "realsense":
        return settings.recording_realsense
    if device_tag == "elgato":
        return settings.recording_elgato
    return settings.recording_other


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
