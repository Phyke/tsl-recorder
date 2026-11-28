# tsl-recorder

The TSL recorder app: the Setup, Record, and Library pages in one window.

This version is the poc1 main app of `realsense-recorder` (commit `93df15b`, the `uv run poc1` app). The user interface uses PySide6 instead of tkinter. The camera, recording, and export code is the poc1 code. Only its import lines are changed.

## Run

```powershell
uv sync
uv run recorder
```

Options:

- `--slots N`: the number of camera slots at the start (2 or more). Default: the "Camera slots at start" setting (2).
- `--out-dir PATH`: the save folder. Default: the last Save folder, else `./recordings/deliverable1`.

## Settings

The Settings tab saves its values in `settings.json` in the project folder. Each station PC has its own file, so git does not track it.

- General: Station (Not set, Station A, Station B). Record writes it into each recording report as `station`.
- General: Camera slots at start (2 to 8). The change applies at the next start.
- Interface: Font size (8 to 24 pt, default 16). The change applies at once.
- Preview: one column for each camera type (RealSense, Elgato, Other). Other is a webcam, the OBS virtual camera, a RealSense opened as UVC, or the fake camera. The change applies at the next preview frame.
  - Preview fps (5, 10, 15, 30, 60, default 15).
  - Preview resolution (x1, x0.75, x0.5, x0.25 of the camera frame). Default: Elgato x0.5, RealSense x0.75, Other x1. The RealSense depth view uses the RealSense value.
  - HUD font size (12 to 32 px for each text line, default 18).
  - HUD color (Green, White, Yellow, Cyan, Red, Black, default Green).
- Recording: one column for each camera type (RealSense, Elgato, Other). A row has a list only for the camera types that use it. The defaults are the values of the code before this page. The note of each row says when a change applies (the next Start preview or the next Record).
  - MP4 queue (default 512 frames) and MP4 queue at the deep rate (default 4800 frames; also for the fake camera). The note shows the RAM of a full queue.
  - Deep rate from (default 60 fps).
  - Driver buffer (default 1 frame) and driver buffer at the deep rate (default 4 frames): Elgato and Other.
  - Elgato only: OBS-pin driver buffer (default 8 frames), ffmpeg input buffer (default 20M), ffmpeg frame queue (default 8 frames), .db3 queue (default 512 frames), .db3 JPEG quality (default 80), .db3 drain limit at Stop (default 25 s).
  - MP4 drain limit at Stop (default 60 s), MP4 codec (default Auto), system monitor interval (default 1000 ms).
  - A change on this page waits for the Apply button at the top of the page. The text next to Apply shows the count of the changes that wait. A reset of this page (or Reset all) also waits for Apply.
  - Apply saves the changes. If a change applies at the next Start preview, Apply stops all running previews, and the status bar tells you to start them again.
  - Apply is off while a preview starts or a recording runs.
  - The changes that wait are not saved. If you close the app before Apply, they are lost.
- The text between the Stop button and the Last report button shows the automatic Record check, with each issue on its own line. Red, with Record off: two or more 1920x1080 cameras at 90 fps or more are armed. Amber, with Record on: an Elgato asked for 90 fps or more but delivers less than 85 % of it (the HDMI signal is not 1080p120).
- The Record tab shows the length of the take next to the Stop button (mm:ss). It counts in red while Record runs. At Stop, it stops and stays in gray until the next Record.
- The Record tab shows a status row for each camera: a colored badge and a detail text. Under it, one line for each queue of the camera: the name, a fill bar, and the count.
  - Gray: Preview off, Not armed. Green: Ready, Saved. Red: Recording. Amber: Starting, Stopping, Draining MP4, Finishing .db3, Saving, Fixing fps. Orange: Saved with drops. Dark red: Failed.
  - The lines, in the order of the frames: Real-time buffer and ffmpeg queue (Elgato through ffmpeg only), MP4 queue, .db3 queue (Elgato, only while a .db3 is open), Recorder queue.
  - A fill bar is green below half full, amber below 90 %, and red from 90 %.
  - Real-time buffer: the ffmpeg `-rtbufsize` buffer. It has no fill bar, because ffmpeg writes a line only when the buffer drops a frame. The line shows the count of these drops and the fill at the last drop. The count starts at Record and stops at Stop. Before the first take, it counts from the preview start. These drops are part of the dropped count of "Saved". The report has them as `dropped_capture_frames`, the name that the validator reads.
  - ffmpeg queue: the frames between the ffmpeg pipe reader and the capture thread. The capture thread takes them in order, the oldest first, so a short stall of the capture thread loses no frame. When the queue is full, the pipe reader removes the oldest frame and counts it. The line shows this count, from Record to Stop (before the first take, from the preview start). The report has it as `dropped_ffmpeg_queue`. It is part of the dropped count of "Saved". Without ffmpeg, both report fields are `null`.
  - MP4 queue, "dropped at Stop": the frames still in the queue when the MP4 drain limit at Stop ends. Stop deletes them. This count is separate from the queue drops ("dropped"). The report has it as `dropped_at_stop`, and the queue drops as `dropped_processor_queue`. Both counts are part of the dropped count of "Saved".
  - Draining MP4: the detail text shows the frames left in the MP4 queue and the seconds left of the MP4 drain limit at Stop.
  - The app cannot read the queue of the RealSense SDK .db3 writer.
- RealSense: each take also writes `meta/<name>_frames.csv`, with one row for each MP4 frame in the MP4 order: `mp4_frame` (the frame index in the MP4), `barcode_seq` (the barcode in the frame), and `camera_frame_number` (the RealSense SDK frame number of the image). The .db3 has the same SDK frame numbers, so the file matches each MP4 frame to its .db3 frame.
- Each page has a "Reset this page to defaults" button, and "Reset all settings to defaults" is under the list of pages. Each button asks first. A reset does not change the Save folder.
- The Save folder on the Setup tab is also saved when it changes.
- If `settings.json` has a bad value, the app starts with the default settings and shows the bad key. The next change on the Settings tab replaces the file.

`src/recorder/config.py` holds all the settings in one object, `settings`. Each module reads `settings.<name>`.

- The fields (`font_size`, `save_folder`, `camera_slots`, `station`, `preview_elgato`, `preview_realsense`, `preview_other`, `recording_realsense`, `recording_elgato`, `recording_other`) come from `settings.json`.
- The fixed values (for example `high_rate_fps`, `ui_tick_ms`, `uvc_preset_modes`) are in git, so both stations use the same values. `settings.json` cannot change them.

## How to use tab

The How to use tab shows the steps, the multi-camera rules, the notes about "Also save bag with MP4", and the notes about the files. These texts were on the Setup tab, on the Record tab, and in each camera card.

## Layout

- `src/recorder/gui.py`: the main window (Setup, Record, and Library pages).
- `src/recorder/config.py`: the settings and the fixed values.
- `src/recorder/cards.py`: the camera card on the Setup page.
- `src/recorder/library.py`: the Library page (browse, play, and export to MP4).
- `src/recorder/review.py`: the review prompt after a recording is saved.
- `src/recorder/theme.py`: the colors and the shared widget helpers.
- `src/recorder/ui_bridge.py`: runs a function on the Qt UI thread from a worker thread.
- All other files in `src/recorder/` are the poc1 modules.
