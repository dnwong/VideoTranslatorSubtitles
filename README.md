# Video Speech Translator to English Subtitles

A Windows desktop tool that opens up to 10 videos at once and translates the spoken audio to English `.srt` subtitle files with the same names. It can optionally burn visible subtitles into new videos ending with `_eng`.

Supported input formats: `.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.avi`, `.wmv`.

## Requirements

- Windows 10 or newer
- Python 3.10 or newer
- FFmpeg is provided by `imageio-ffmpeg`, which the requirements install; a system FFmpeg on `PATH` is only a fallback

## Install FFmpeg

1. Download FFmpeg from <https://www.gyan.dev/ffmpeg/builds/>.
2. Extract it somewhere permanent, for example `C:\ffmpeg`.
3. Add the `bin` folder, for example `C:\ffmpeg\bin`, to your Windows `PATH`.
4. Open a new PowerShell window and confirm this works:

```powershell
ffmpeg -version
```

## Setup

From this project folder, run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The first translation will download the selected Whisper model. Larger models need more disk space and take longer.

## Run

```powershell
python app.py
```

## Run the Windows executable

Each build goes into a new timestamped folder, so use the folder your build printed. For example:

```text
dist_<timestamp>\VideoTranslatorSubtitles\VideoTranslatorSubtitles.exe
```

To move the app to another Windows computer, copy the whole folder:

```text
dist_<timestamp>\VideoTranslatorSubtitles\
```

The executable build includes FFmpeg through `imageio-ffmpeg`, so the other computer should not need a separate FFmpeg install.

## Build the Windows executable

Run:

```powershell
.\build_exe.bat
```

This calls `auto_build.ps1`, which installs the requirements, compiles `app.py` and builds with PyInstaller. Every build goes into a new folder named with the build time (`yyyyMMdd_HHmmss`), so an earlier build is never overwritten:

```text
dist_<timestamp>\VideoTranslatorSubtitles\VideoTranslatorSubtitles.exe
```

The executable needs the rest of that folder beside it, so copy the folder rather than the `.exe` alone. To hand over a single file instead, see the portable build below.

## Portable single-file build

Run:

```powershell
.\build_portable.bat
```

This calls `auto_build_portable.ps1` (PyInstaller `--onefile`). Like the folder build it writes a new timestamped folder each time, and the single executable sits directly inside it:

```text
dist_portable_<timestamp>\VideoTranslatorSubtitles.exe
```

Copy that single file to any Windows computer and run it. No folder, no Python install and no separate FFmpeg are needed; FFmpeg and the Whisper libraries are packed inside the executable.

The first launch unpacks the app to the `%TEMP%` folder (about 325 MB), so startup is slower than the folder build. Downloaded Whisper models are still cached in `%LOCALAPPDATA%\VideoTranslatorSubtitles\huggingface`, so that computer needs internet once per model.

## Self-test the packaged build

The executable has an opt-in `--selftest` flag that processes one video without opening the GUI, so a frozen build can be verified end to end:

```text
VideoTranslatorSubtitles.exe --selftest <video> <log file> [srt|burn]
```

- `srt` writes only the `.srt` subtitle file; `burn` also writes the `_eng` video, scaled to the height selected in the app when re-encoding is enabled.
- The log file records the resolved FFmpeg path, progress, the output path and size, and any failure.
- The exit code is `0` when the batch produced output and `1` otherwise.
- Set `VTS_SELFTEST_MODEL` to override the default `tiny` model.

Without the flag the app starts normally.

## Saved preferences

The app remembers the selected model, the burn and re-encode checkboxes, the chosen height and the skip-existing setting, and reloads them the next time it starts:

```text
%LOCALAPPDATA%\VideoTranslatorSubtitles\settings.json
```

Delete that file to return to the defaults: `small` model, `720p`, burn-in enabled and skip-existing enabled.

The FFmpeg bundled with the app is always preferred over an `ffmpeg` found on `PATH`, so every computer runs the same version the build was tested with.

## How to use

1. Add up to 10 videos (`.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.avi`, `.wmv`): click **Browse**, or drop the files on the window or on the list. Repeating **Browse** adds more; **Clear** empties the list.
2. Reorder or trim the queue with **Move up**, **Move down** and **Remove**; files are processed from the top down.
3. Select a Whisper model. A new install starts on `small`, and the app remembers whatever you choose.
4. **Burn subtitles visibly into a new video ending with "_eng"** is checked by default.
5. **Re-encode burned video to the selected height** is checked by default; choose `1080`, `720` or `480` from the list right beside it.
6. **Skip videos that already have their output file** is checked by default, so re-running a batch never overwrites finished work. With it turned off, new outputs get a ` (2)` suffix instead of replacing the earlier file.
7. Click **Create Subtitle Files**. While a batch runs, the status shows the current file and an estimate of the time remaining; **Cancel** stops the queue and terminates any FFmpeg process still encoding; files that already finished are kept.

Each queued video is processed in order. When burning is not enabled, the app creates an `.srt` subtitle file with the English translation and the same base name as the original video. When burning is enabled, it creates a new video ending with `_eng` where the subtitles are visible, then deletes the separate `.srt` file. If re-encoding is enabled, the burned output video is scaled to the selected height. The Whisper model is loaded once per batch instead of once per file, the burn-in step reports its own percentage as FFmpeg writes it, and progress is shown for the whole batch. A file that fails is reported without stopping the remaining files.

When the batch finishes, the app lists the completed files and asks whether to delete their original videos, requiring a second confirmation before anything is deleted.

## Notes

- Translation quality depends on the Whisper model size and source audio clarity.
- The app runs Whisper on CPU by default, so longer videos can take a while.
