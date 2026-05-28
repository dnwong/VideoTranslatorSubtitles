# MP4 Speech Translator to English Subtitles

A Windows desktop tool that opens an MP4 video and translates the spoken audio to an English `.srt` subtitle file with the same name. It can optionally burn visible subtitles into a new MP4 ending with `_eng.mp4`.

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

## How to use

1. Click **Browse** and choose an `.mp4` video.
2. Select a Whisper model.
3. Optional: check **Burn subtitles visibly into a new video ending with "_eng.mp4"**.
4. Optional: check **Re-encode output video to 720p**.
5. Click **Create Subtitle File**.

If burning is not enabled, the app creates an `.srt` subtitle file with the English translation and the same base name as the original video. If burning is enabled, it creates a new MP4 ending with `_eng.mp4` where the subtitles are visible on the video, then deletes the separate `.srt` file. If 720p re-encoding is enabled, the burned output video is scaled to 720p. After successful processing, the app asks whether to delete the original MP4 and requires a second confirmation before deletion.

## Notes

- Translation quality depends on the Whisper model size and source audio clarity.
- The app runs Whisper on CPU by default, so longer videos can take a while.
