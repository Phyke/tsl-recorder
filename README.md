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
- The Save folder on the Setup tab is also saved when it changes.
- If `settings.json` has a bad value, the app starts with the default settings and shows the bad key. The next change on the Settings tab replaces the file.

`src/recorder/config.py` holds all the settings in one object, `settings`. Each module reads `settings.<name>`.

- The fields (`font_size`, `save_folder`, `camera_slots`, `station`) come from `settings.json`.
- The fixed values (for example `dshow_rtbufsize`, `preview_hz`, `uvc_preset_modes`) are in git, so both stations use the same values. `settings.json` cannot change them.

## Layout

- `src/recorder/gui.py`: the main window (Setup, Record, and Library pages).
- `src/recorder/config.py`: the settings and the fixed values.
- `src/recorder/cards.py`: the camera card on the Setup page.
- `src/recorder/library.py`: the Library page (browse, play, and export to MP4).
- `src/recorder/review.py`: the review prompt after a recording is saved.
- `src/recorder/theme.py`: the colors and the shared widget helpers.
- `src/recorder/ui_bridge.py`: runs a function on the Qt UI thread from a worker thread.
- All other files in `src/recorder/` are the poc1 modules.
