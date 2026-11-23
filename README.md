# tsl-recorder

The TSL recorder app: the Setup, Record, and Library pages in one window.

This version is the poc1 main app of `realsense-recorder` (commit `93df15b`, the `uv run poc1` app). The user interface uses PySide6 instead of tkinter. The camera, recording, and export code is the poc1 code. Only its import lines are changed.

## Run

```powershell
uv sync
uv run recorder
```

Options:

- `--slots N`: the number of camera slots at the start (2 or more, default 2).
- `--out-dir PATH`: the save folder (default `./recordings/deliverable1`).

## Layout

- `src/recorder/gui.py`: the main window (Setup, Record, and Library pages).
- `src/recorder/cards.py`: the camera card on the Setup page.
- `src/recorder/library.py`: the Library page (browse, play, and export to MP4).
- `src/recorder/review.py`: the review prompt after a recording is saved.
- `src/recorder/theme.py`: the colors and the shared widget helpers.
- `src/recorder/ui_bridge.py`: runs a function on the Qt UI thread from a worker thread.
- All other files in `src/recorder/` are the poc1 modules.
