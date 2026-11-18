# Video Speech Translator to English Subtitles

A Windows desktop tool that opens up to 10 videos at once and translates the spoken audio to English `.srt` subtitle files with the same names. It can optionally burn visible subtitles into new videos ending with `_eng`.

Supported input formats: `.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.avi`, `.wmv`.

## Requirements

- Windows 10 or newer
- Python 3.10 or newer
- FFmpeg installed and available in your `PATH` when running from Python

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

After building, run:

```text
dist\VideoTranslatorSubtitles\VideoTranslatorSubtitles.exe
```

To move the app to another Windows computer, copy the whole folder:

```text
dist\VideoTranslatorSubtitles\
```

The executable build includes FFmpeg through `imageio-ffmpeg`, so the other computer should not need a separate FFmpeg install.

## Build the Windows executable

Run:

```powershell
.\build_exe.bat
```

The executable will be created at:

```text
dist\VideoTranslatorSubtitles\VideoTranslatorSubtitles.exe
```

## Portable single-file build

Run:

```powershell
.\build_portable.bat
```

The result is one self-contained file:

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

## How to use

1. Click **Browse** and choose up to 10 video files (`.mp4`, `.m4v`, `.mov`, `.mkv`, `.webm`, `.avi`, `.wmv`). Repeat **Browse** to add more; **Clear** empties the list.
2. Select a Whisper model.
3. **Burn subtitles visibly into a new video ending with "_eng"** is checked by default.
4. **Re-encode burned video to the selected height** is checked by default; choose `1080`, `720` or `480` from the list right beside it.
5. Click **Create Subtitle Files**. While a batch runs, **Cancel** stops the queue and terminates any FFmpeg process still encoding; files that already finished are kept.

Each queued video is processed in order. When burning is not enabled, the app creates an `.srt` subtitle file with the English translation and the same base name as the original video. When burning is enabled, it creates a new video ending with `_eng` where the subtitles are visible, then deletes the separate `.srt` file. If re-encoding is enabled, the burned output video is scaled to the selected height. Progress is shown for the whole batch, and a file that fails is reported without stopping the remaining files.

When the batch finishes, the app lists the completed files and asks whether to delete their original videos, requiring a second confirmation before anything is deleted.

## Notes

- Translation quality depends on the Whisper model size and source audio clarity.
- The app runs Whisper on CPU by default, so longer videos can take a while.
