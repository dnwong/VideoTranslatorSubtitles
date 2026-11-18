import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # drag and drop stays optional if the package is missing
    DND_FILES = None
    TkinterDnD = None

os.environ.setdefault("HF_HOME", str(Path.home() / "AppData" / "Local" / "VideoTranslatorSubtitles" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import imageio_ffmpeg
from faster_whisper import WhisperModel

MAX_BATCH_FILES = 10
SUPPORTED_VIDEO_SUFFIXES = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv")
OUTPUT_HEIGHT_CHOICES = ("1080", "720", "480")
MODEL_CHOICES = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_SETTINGS = {
    "model_size": "small",
    "burn_subtitles": True,
    "reencode_video": True,
    "output_height": "720",
    "skip_existing": True,
}
SETTINGS_DIRECTORY = (
    Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    / "VideoTranslatorSubtitles"
)
SETTINGS_FILE = SETTINGS_DIRECTORY / "settings.json"


def load_settings() -> dict:
    """Return the saved preferences, ignoring anything unrecognised."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return settings
    if not isinstance(stored, dict):
        return settings
    if stored.get("model_size") in MODEL_CHOICES:
        settings["model_size"] = stored["model_size"]
    if stored.get("output_height") in OUTPUT_HEIGHT_CHOICES:
        settings["output_height"] = stored["output_height"]
    for key in ("burn_subtitles", "reencode_video", "skip_existing"):
        if isinstance(stored.get(key), bool):
            settings[key] = stored[key]
    return settings


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_DIRECTORY.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception:
        pass


def create_root() -> tk.Tk:
    """Build the Tk root, using the drag-and-drop aware class when available."""
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except Exception:
            pass
    return tk.Tk()


@dataclass
class TranslatedSegment:
    index: int
    start: float
    end: float
    text: str


class ConversionCancelled(Exception):
    """Raised when the user cancels the running batch."""


class VideoTranslatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Video Speech Translator to English Subtitles")
        self.root.geometry("840x700")
        self.root.minsize(780, 640)

        settings = load_settings()
        self.input_files: list[Path] = []
        self.model_size = tk.StringVar(value=str(settings["model_size"]))
        self.burn_subtitles = tk.BooleanVar(value=bool(settings["burn_subtitles"]))
        self.reencode_video = tk.BooleanVar(value=bool(settings["reencode_video"]))
        self.output_height = tk.StringVar(value=str(settings["output_height"]))
        self.skip_existing = tk.BooleanVar(value=bool(settings["skip_existing"]))
        self.status_text = tk.StringVar(value="Choose up to 10 video files to begin.")
        self.progress_value = tk.DoubleVar(value=0)
        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.process_lock = threading.Lock()
        self.active_process: subprocess.Popen | None = None
        self.cached_model = None
        self.cached_model_key: tuple[str, str, str] | None = None
        self.persist_settings = True
        self.batch_started: float | None = None
        self.finished_durations: list[float] = []

        self._build_ui()
        for variable in (
            self.model_size,
            self.burn_subtitles,
            self.reencode_video,
            self.output_height,
            self.skip_existing,
        ):
            variable.trace_add("write", self._on_settings_changed)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_queue)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(frame, text="Video Speech Translator to English Subtitles", font=("Segoe UI", 18, "bold"))
        title.pack(anchor=tk.W)

        subtitle = ttk.Label(
            frame,
            text=(
                "Open up to 10 videos and translate the spoken language to English .srt subtitle files. "
                'Optionally burn subtitles into new _eng videos.'
            ),
            wraplength=760,
        )
        subtitle.pack(anchor=tk.W, pady=(4, 14))

        input_row = ttk.Frame(frame)
        input_row.pack(fill=tk.X, pady=6)
        ttk.Label(input_row, text="Input videos", width=14).pack(side=tk.LEFT, anchor=tk.N)

        list_frame = ttk.Frame(input_row)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)
        self.file_list = tk.Listbox(list_frame, height=5, activestyle="none", font=("Segoe UI", 9))
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_list.yview)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.file_list.configure(yscrollcommand=scrollbar.set)

        button_column = ttk.Frame(input_row)
        button_column.pack(side=tk.LEFT, anchor=tk.N)
        ttk.Button(button_column, text="Browse", command=self._choose_inputs).pack(fill=tk.X)
        ttk.Button(button_column, text="Remove", command=self._remove_selected).pack(fill=tk.X, pady=(4, 0))
        ttk.Button(button_column, text="Clear", command=self._clear_inputs).pack(fill=tk.X, pady=(4, 0))
        ttk.Button(button_column, text="Move up", command=lambda: self._move_selected(-1)).pack(fill=tk.X, pady=(10, 0))
        ttk.Button(button_column, text="Move down", command=lambda: self._move_selected(1)).pack(fill=tk.X, pady=(4, 0))

        model_row = ttk.Frame(frame)
        model_row.pack(fill=tk.X, pady=6)
        ttk.Label(model_row, text="Whisper model", width=14).pack(side=tk.LEFT)
        model_box = ttk.Combobox(
            model_row,
            textvariable=self.model_size,
            values=MODEL_CHOICES,
            state="readonly",
            width=18,
        )
        model_box.pack(side=tk.LEFT, padx=8)
        ttk.Label(model_row, text="Use tiny/base for faster processing. Larger models are much slower on CPU.").pack(side=tk.LEFT)

        burn_row = ttk.Frame(frame)
        burn_row.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            burn_row,
            text='Burn subtitles visibly into a new video ending with "_eng"',
            variable=self.burn_subtitles,
        ).pack(anchor=tk.W)

        output_row = ttk.Frame(frame)
        output_row.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            output_row,
            text="Re-encode burned video to the selected height",
            variable=self.reencode_video,
        ).pack(side=tk.LEFT)
        ttk.Combobox(
            output_row,
            textvariable=self.output_height,
            values=OUTPUT_HEIGHT_CHOICES,
            state="readonly",
            width=8,
        ).pack(side=tk.LEFT, padx=(8, 0))

        skip_row = ttk.Frame(frame)
        skip_row.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            skip_row,
            text="Skip videos that already have their output file (never overwrite)",
            variable=self.skip_existing,
        ).pack(anchor=tk.W)

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(18, 8))
        self.convert_button = ttk.Button(actions, text="Create Subtitle Files", command=self._start_conversion)
        self.convert_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self._request_cancel, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))

        self.progress = ttk.Progressbar(frame, variable=self.progress_value, maximum=100)
        self.progress.pack(fill=tk.X, pady=(12, 8))

        status_box = ttk.LabelFrame(frame, text="Status", padding=10)
        status_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.status_label = ttk.Label(status_box, textvariable=self.status_text, wraplength=740, justify=tk.LEFT)
        self.status_label.pack(anchor=tk.NW, fill=tk.BOTH, expand=True)

        self._bind_drag_and_drop()

    def _choose_inputs(self) -> None:
        selected = filedialog.askopenfilenames(
            filetypes=[
                ("Video files", " ".join(f"*{suffix}" for suffix in SUPPORTED_VIDEO_SUFFIXES)),
                ("All files", "*.*"),
            ]
        )
        if not selected:
            return
        self._add_files(selected)

    def _add_files(self, paths) -> None:
        unsupported = []
        duplicates = 0
        over_limit = 0
        for raw_path in paths:
            path = Path(raw_path)
            if path.suffix.lower() not in SUPPORTED_VIDEO_SUFFIXES:
                unsupported.append(path.name)
                continue
            if path in self.input_files:
                duplicates += 1
                continue
            if len(self.input_files) >= MAX_BATCH_FILES:
                over_limit += 1
                continue
            self.input_files.append(path)

        self._refresh_file_list()

        messages = []
        if over_limit:
            messages.append(
                f"Only {MAX_BATCH_FILES} files can be queued at a time, so {over_limit} file(s) were not added."
            )
        if unsupported:
            preview = ", ".join(unsupported[:5])
            messages.append(f"Skipped unsupported file type: {preview}")
        if duplicates:
            messages.append(f"Ignored {duplicates} file(s) that were already in the list.")
        if messages:
            messagebox.showwarning("Some files were not added", "\n\n".join(messages), parent=self.root)
        self.status_text.set(f"{len(self.input_files)} file(s) queued. Click Create Subtitle Files to start.")

    def _clear_inputs(self) -> None:
        self.input_files.clear()
        self._refresh_file_list()
        self.progress_value.set(0)
        self.status_text.set("Choose up to 10 video files to begin.")

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, tk.END)
        for index, path in enumerate(self.input_files, start=1):
            self.file_list.insert(tk.END, f"{index}. {path.name}  —  {path.parent}")

    def _remove_selected(self) -> None:
        selected = sorted(self.file_list.curselection(), reverse=True)
        if not selected:
            self.status_text.set("Select a file in the list to remove it.")
            return
        for index in selected:
            if 0 <= index < len(self.input_files):
                del self.input_files[index]
        self._refresh_file_list()
        self.status_text.set(f"{len(self.input_files)} file(s) queued.")

    def _move_selected(self, offset: int) -> None:
        selected = list(self.file_list.curselection())
        if len(selected) != 1:
            self.status_text.set("Select one file in the list to move it.")
            return
        index = selected[0]
        target = index + offset
        if not 0 <= target < len(self.input_files):
            return
        self.input_files[index], self.input_files[target] = self.input_files[target], self.input_files[index]
        self._refresh_file_list()
        self.file_list.selection_set(target)
        self.file_list.activate(target)
        self.status_text.set(f"{len(self.input_files)} file(s) queued.")

    def _bind_drag_and_drop(self) -> None:
        if DND_FILES is None:
            return
        try:
            self.file_list.drop_target_register(DND_FILES)
            self.file_list.dnd_bind("<<Drop>>", self._on_drop)
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event):
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            cleaned = str(event.data).replace("{", "").replace("}", "")
            paths = [part for part in cleaned.split() if part]
        if paths:
            self._add_files(paths)

    def _settings_payload(self) -> dict:
        return {
            "model_size": self.model_size.get(),
            "burn_subtitles": bool(self.burn_subtitles.get()),
            "reencode_video": bool(self.reencode_video.get()),
            "output_height": self.output_height.get(),
            "skip_existing": bool(self.skip_existing.get()),
        }

    def _on_settings_changed(self, *_args) -> None:
        if not self.persist_settings:
            return
        save_settings(self._settings_payload())

    def _on_close(self) -> None:
        save_settings(self._settings_payload())
        self.root.destroy()

    def _model_is_cached(self, model_size: str) -> bool:
        return self.cached_model is not None and self.cached_model_key == (model_size, "cpu", "int8")

    def _get_model(self, model_size: str):
        """Load Whisper once per batch instead of once per file."""
        if not self._model_is_cached(model_size):
            self._release_model()
            self.cached_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            self.cached_model_key = (model_size, "cpu", "int8")
        return self.cached_model

    def _release_model(self) -> None:
        self.cached_model = None
        self.cached_model_key = None

    def _planned_output_path(self, input_file: Path, burn_subtitles: bool) -> Path:
        if burn_subtitles:
            return input_file.with_name(f"{input_file.stem}_eng{input_file.suffix}")
        return input_file.with_suffix(".srt")

    def _unique_path(self, path: Path) -> Path:
        """Return a name that does not exist yet, so earlier output is never overwritten."""
        if not path.exists():
            return path
        for counter in range(2, 1000):
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Could not find a free file name for {path.name}.")

    def _start_conversion(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            messagebox.showinfo("Still running", "A conversion is still finishing. Please wait a moment.", parent=self.root)
            return

        files = list(self.input_files)
        if not files:
            messagebox.showerror("No input", "Please choose at least one video file.")
            return

        missing = [str(path) for path in files if not path.exists()]
        if missing:
            messagebox.showerror("Invalid input", "These files no longer exist:\n\n" + "\n".join(missing))
            return

        if self._get_ffmpeg_path() is None:
            messagebox.showerror("FFmpeg not found", "FFmpeg is not available. Rebuild the app or install FFmpeg.")
            return

        self.cancel_event.clear()
        self.convert_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.progress_value.set(0)
        self.status_text.set("Starting conversion...")
        reencode_height = int(self.output_height.get()) if self.reencode_video.get() else None
        self.worker_thread = threading.Thread(
            target=self._process_batch,
            args=(
                files,
                self.model_size.get(),
                self.burn_subtitles.get(),
                reencode_height,
                self.skip_existing.get(),
            ),
            daemon=True,
        )
        self.worker_thread.start()

    def _request_cancel(self) -> None:
        if self.worker_thread is None or not self.worker_thread.is_alive():
            return
        self.cancel_event.set()
        self.cancel_button.configure(state=tk.DISABLED)
        self.status_text.set("Cancelling... stopping the current step.")
        with self.process_lock:
            process = self.active_process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise ConversionCancelled()

    def _process_batch(
        self,
        files: list[Path],
        model_size: str,
        burn_subtitles: bool,
        reencode_height: int | None,
        skip_existing: bool = True,
    ) -> None:
        results: list[tuple[Path, Path]] = []
        failures: list[tuple[Path, str]] = []
        skipped: list[tuple[Path, Path]] = []
        cancelled = False
        self.batch_started = time.monotonic()
        self.finished_durations = []
        try:
            total = len(files)
            for index, input_file in enumerate(files):
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                planned_output = self._planned_output_path(input_file, burn_subtitles)
                if skip_existing and planned_output.exists():
                    skipped.append((input_file, planned_output))
                    self._post_progress(
                        f"[{index + 1}/{total}] {input_file.name}\nSkipped: {planned_output.name} already exists.",
                        (index + 1) / total * 100.0,
                        index,
                        total,
                    )
                    continue
                file_started = time.monotonic()
                try:
                    output_file = self._convert_file(
                        input_file,
                        model_size,
                        burn_subtitles,
                        reencode_height,
                        index,
                        total,
                    )
                    results.append((input_file, output_file))
                except ConversionCancelled:
                    cancelled = True
                    break
                except Exception as exc:
                    failures.append((input_file, str(exc)))
                finally:
                    self.finished_durations.append(time.monotonic() - file_started)
            self.message_queue.put(("batch_done", (results, failures, cancelled, skipped)))
        except Exception as exc:
            self.message_queue.put(("error", str(exc)))
        finally:
            self._release_model()
            self.batch_started = None

    def _convert_file(
        self,
        input_file: Path,
        model_size: str,
        burn_subtitles: bool,
        reencode_height: int | None,
        index: int,
        total: int,
    ) -> Path:
        label = f"[{index + 1}/{total}] {input_file.name}"
        self._raise_if_cancelled()
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_audio = temp_dir / "source.wav"

            self._post_progress(f"{label}\nExtracting audio...", 5, index, total)
            self._run_ffmpeg([
                self._get_ffmpeg_path(),
                "-y",
                "-i",
                str(input_file),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(source_audio),
            ])

            if not self._model_is_cached(model_size):
                self._post_progress(f"{label}\nLoading Whisper model '{model_size}'...", 15, index, total)
            model = self._get_model(model_size)
            self._raise_if_cancelled()

            self._post_progress(
                f"{label}\nTranslating speech to English text. This can take several minutes on CPU...",
                20,
                index,
                total,
            )
            raw_segments, info = model.transcribe(
                str(source_audio),
                task="translate",
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            segments = []
            duration = max(float(info.duration or 1), 1.0)
            for raw_segment in raw_segments:
                text = raw_segment.text.strip()
                if not text:
                    continue
                segments.append(TranslatedSegment(len(segments) + 1, raw_segment.start, raw_segment.end, text))
                progress = 20 + min(raw_segment.end / duration, 1.0) * 50
                self._post_progress(
                    f"{label}\nProcessed {raw_segment.end:.1f} of {duration:.1f} seconds.",
                    progress,
                    index,
                    total,
                )
                self._raise_if_cancelled()

            if not segments:
                raise RuntimeError("No speech was detected in the video.")

            self._raise_if_cancelled()

            srt_content = self._segments_to_srt(segments)
            if burn_subtitles:
                embedded_video = self._unique_path(
                    input_file.with_name(f"{input_file.stem}_eng{input_file.suffix}")
                )
                description = f" at {reencode_height}p" if reencode_height else ""
                self._post_progress(
                    f"{label}\nBurning subtitles into new video{description}. This may take a while...",
                    80,
                    index,
                    total,
                )
                temp_embed_srt = temp_dir / "english.srt"
                temp_embed_srt.write_text(srt_content, encoding="utf-8")
                try:
                    self._embed_subtitles(
                        input_file,
                        temp_embed_srt,
                        embedded_video,
                        reencode_height,
                        duration=duration,
                        label=label,
                        index=index,
                        total=total,
                    )
                except ConversionCancelled:
                    embedded_video.unlink(missing_ok=True)
                    raise
                output_file = embedded_video
            else:
                self._post_progress(f"{label}\nSaving English subtitles...", 75, index, total)
                output_file = self._unique_path(input_file.with_suffix(".srt"))
                output_file.write_text(srt_content, encoding="utf-8")

            self._post_progress(f"{label}\nFinished.", 100, index, total)
            return output_file

    def _segments_to_srt(self, segments: list[TranslatedSegment]) -> str:
        blocks = []
        for segment in segments:
            blocks.append(
                f"{segment.index}\n"
                f"{self._format_srt_time(segment.start)} --> {self._format_srt_time(segment.end)}\n"
                f"{segment.text}\n"
            )
        return "\n".join(blocks)

    def _format_srt_time(self, seconds: float) -> str:
        milliseconds = int(round(seconds * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    def _embed_subtitles(
        self,
        input_file: Path,
        srt_file: Path,
        output_file: Path,
        reencode_height: int | None = None,
        duration: float = 0.0,
        label: str = "",
        index: int = 0,
        total: int = 1,
    ) -> None:
        subtitle_filter = f"subtitles={self._format_ffmpeg_filter_path(srt_file)}"
        video_filter = subtitle_filter
        if reencode_height:
            video_filter = f"scale=-2:{reencode_height},{subtitle_filter}"
        self._run_ffmpeg_with_progress(
            [
                self._get_ffmpeg_path(),
                "-y",
                "-i",
                str(input_file),
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-crf",
                "23",
                "-preset",
                "veryfast",
                "-c:a",
                "copy",
                "-progress",
                "pipe:1",
                "-nostats",
                str(output_file),
            ],
            duration,
            label,
            index,
            total,
        )

    def _format_ffmpeg_filter_path(self, path: Path) -> str:
        formatted = path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
        return f"'{formatted}'"

    def _windows_process_flags(self):
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 6
            creationflags = subprocess.CREATE_NO_WINDOW
        return startupinfo, creationflags

    def _spawn_ffmpeg(self, command: list[str]) -> subprocess.Popen:
        startupinfo, creationflags = self._windows_process_flags()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        with self.process_lock:
            self.active_process = process
            already_cancelled = self.cancel_event.is_set()
        if already_cancelled and process.poll() is None:
            process.terminate()
        return process

    def _finish_ffmpeg(self, process: subprocess.Popen, command: list[str], stdout: str, stderr: str) -> None:
        with self.process_lock:
            self.active_process = None
        if self.cancel_event.is_set():
            raise ConversionCancelled()
        if process.returncode != 0:
            command_text = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)
            error_text = (stderr or stdout or "").strip() or "FFmpeg failed."
            raise RuntimeError(f"FFmpeg failed while opening or writing a file.\n\nCommand:\n{command_text}\n\nDetails:\n{error_text}")

    def _run_ffmpeg(self, command: list[str]) -> None:
        process = self._spawn_ffmpeg(command)
        try:
            stdout, stderr = process.communicate()
        finally:
            with self.process_lock:
                self.active_process = None
        self._finish_ffmpeg(process, command, stdout, stderr)

    def _run_ffmpeg_with_progress(
        self,
        command: list[str],
        duration: float,
        label: str,
        index: int,
        total: int,
        start_percent: float = 80.0,
        end_percent: float = 99.0,
    ) -> None:
        """Run FFmpeg and report the encode position from its `-progress` stream."""
        process = self._spawn_ffmpeg(command)
        stderr_chunks: list[str] = []

        def drain_stderr() -> None:
            if process.stderr is None:
                return
            for line in process.stderr:
                stderr_chunks.append(line)
                if len(stderr_chunks) > 4000:
                    del stderr_chunks[:2000]

        reader = threading.Thread(target=drain_stderr, daemon=True)
        reader.start()
        speed_text = ""
        last_post = 0.0
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    if self.cancel_event.is_set():
                        if process.poll() is None:
                            process.terminate()
                        break
                    key, _, value = line.strip().partition("=")
                    if key == "speed":
                        speed_text = value.strip()
                    elif key == "out_time_us" and duration > 0:
                        try:
                            seconds = int(value) / 1_000_000
                        except ValueError:
                            continue
                        fraction = max(min(seconds / duration, 1.0), 0.0)
                        now = time.monotonic()
                        if now - last_post < 0.4:
                            continue
                        last_post = now
                        detail = f"{fraction * 100:.0f}% of the video"
                        if speed_text:
                            detail += f" at {speed_text}"
                        self._post_progress(
                            f"{label}\nBurning subtitles into new video: {detail}.",
                            start_percent + fraction * (end_percent - start_percent),
                            index,
                            total,
                        )
            process.wait()
        finally:
            reader.join(timeout=2)
        self._finish_ffmpeg(process, command, "", "".join(stderr_chunks))

    def _get_ffmpeg_path(self) -> str | None:
        """Use the FFmpeg shipped with the app first, so every build behaves the same."""
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
        return shutil.which("ffmpeg")

    def _post_progress(self, text: str, local_progress: float, index: int, total: int) -> None:
        clamped = max(min(float(local_progress), 100.0), 0.0)
        overall = (index + clamped / 100.0) / total * 100.0
        self.message_queue.put(("status", (f"{text}{self._eta_text(index, total, clamped)}", overall)))

    def _eta_text(self, index: int, total: int, local_progress: float) -> str:
        if self.batch_started is None or not self.finished_durations:
            return ""
        average = sum(self.finished_durations) / len(self.finished_durations)
        remaining_files = max(total - index - 1, 0) + (1.0 - local_progress / 100.0)
        seconds = average * remaining_files
        if seconds < 20:
            return ""
        return f"\nAbout {self._format_duration(seconds)} remaining."

    def _format_duration(self, seconds: float) -> str:
        total_seconds = int(round(seconds))
        if total_seconds < 60:
            return f"{max(total_seconds, 1)} s"
        minutes, _seconds = divmod(total_seconds, 60)
        if minutes < 60:
            return f"{minutes} min"
        hours, minutes = divmod(minutes, 60)
        return f"{hours} h {minutes:02d} min"

    def _process_queue(self) -> None:
        try:
            while True:
                event, payload = self.message_queue.get_nowait()
                if event == "status":
                    text, progress = payload
                    self.status_text.set(str(text))
                    self.progress_value.set(float(progress))
                elif event == "batch_done":
                    results, failures, cancelled, skipped = payload
                    if not cancelled:
                        self.progress_value.set(100)
                    self.convert_button.configure(state=tk.NORMAL)
                    self.cancel_button.configure(state=tk.DISABLED)
                    summary = self._format_batch_summary(results, failures, cancelled, skipped)
                    self.status_text.set(summary)
                    if cancelled:
                        messagebox.showinfo("Batch cancelled", summary, parent=self.root)
                    elif failures:
                        messagebox.showwarning("Batch finished with problems", summary, parent=self.root)
                    else:
                        messagebox.showinfo("Conversion complete", summary, parent=self.root)
                    if results:
                        completed = list(results)
                        self.root.after(100, lambda pairs=completed: self._ask_delete_originals(pairs))
                elif event == "error":
                    self.status_text.set(f"Error: {payload}")
                    self.convert_button.configure(state=tk.NORMAL)
                    self.cancel_button.configure(state=tk.DISABLED)
                    messagebox.showerror("Conversion failed", str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def _format_batch_summary(
        self,
        results: list[tuple[Path, Path]],
        failures: list[tuple[Path, str]],
        cancelled: bool = False,
        skipped: list[tuple[Path, Path]] | None = None,
    ) -> str:
        skipped = skipped or []
        lines = []
        if cancelled:
            lines.append("Batch cancelled. Remaining files were not processed.")
        if results:
            if lines:
                lines.append("")
            lines.append(f"Finished {len(results)} file(s):")
            lines.extend(f"  {output_file}" for _input, output_file in results)
        if skipped:
            if lines:
                lines.append("")
            lines.append(f"Skipped {len(skipped)} file(s) that already had an output:")
            lines.extend(f"  {output_file}" for _input, output_file in skipped)
        if failures:
            if lines:
                lines.append("")
            lines.append(f"Failed {len(failures)} file(s):")
            for input_file, error in failures:
                first_line = error.strip().splitlines()[0] if error.strip() else "Unknown error"
                lines.append(f"  {input_file.name}: {first_line}")
        return "\n".join(lines) if lines else "Nothing was processed."

    def _ask_delete_originals(self, completed: list[tuple[Path, Path]]) -> None:
        existing = [(original, output) for original, output in completed if original.exists()]
        if not existing:
            self.status_text.set("No original videos left to delete.")
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        file_details = self._format_file_details(existing)
        if len(existing) == 1:
            question = "Do you want to delete the original video file?"
            confirm_message = "Are you sure you want to delete the file?"
        else:
            question = f"Do you want to delete the {len(existing)} original video files?"
            confirm_message = f"Are you sure you want to delete these {len(existing)} files?"
        should_delete = self._ask_wide_yes_no(
            "Delete original videos?",
            f"{question}\n\n{file_details}",
            confirm_message=confirm_message,
        )
        if not should_delete:
            return

        deleted = []
        failed = []
        for original, _output in existing:
            try:
                original.unlink()
                deleted.append(original)
            except Exception as exc:
                failed.append((original, str(exc)))

        if failed:
            details = "\n\n".join(f"{path}\n{error}" for path, error in failed)
            messagebox.showerror(
                "Delete failed",
                f"Could not delete these files:\n\n{details}",
                parent=self.root,
            )
        if deleted:
            deleted_message = "Deleted original video file(s):\n\n" + "\n".join(str(path) for path in deleted)
            self.status_text.set(deleted_message)
            if not failed:
                messagebox.showinfo("Originals deleted", deleted_message, parent=self.root)

    def _format_file_details(self, pairs: list[tuple[Path, Path]]) -> str:
        rows = []
        for original_file, output_file in pairs:
            rows.append(("Old file", original_file))
            rows.append(("New file", output_file))
        filename_width = max([42] + [len(path.name) for _label, path in rows])
        lines = [
            f"{'Type':<10} {'Filename':<{filename_width}} {'Date modified':<20} {'Size':>12}",
            f"{'-' * 10} {'-' * filename_width} {'-' * 20} {'-' * 12}",
        ]
        for label, path in rows:
            if path.exists():
                stats = path.stat()
                modified = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %I:%M %p")
                size = self._format_file_size(stats.st_size)
            else:
                modified = "Missing"
                size = "Missing"
            name = path.name
            lines.append(f"{label:<10} {name:<{filename_width}} {modified:<20} {size:>12}")
        return "\n".join(lines)

    def _format_file_size(self, size: int) -> str:
        units = ("B", "KB", "MB", "GB", "TB")
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024

    def _ask_wide_yes_no(self, title: str, message: str, warning_text: str | None = None, confirm_message: str | None = None) -> bool:
        result = tk.BooleanVar(value=False)
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)

        display_message = message
        if warning_text and message.endswith(warning_text):
            display_message = message[: -len(warning_text)].rstrip()

        message_label = ttk.Label(frame, text=display_message, justify=tk.LEFT, font=("Lucida Console", 10))
        message_label.pack(fill=tk.BOTH, expand=True)

        if warning_text:
            warning_label = tk.Label(frame, text=warning_text, justify=tk.LEFT, font=("Segoe UI", 16, "bold"), fg="red")
            warning_label.pack(anchor=tk.W, pady=(10, 0))

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X, pady=(18, 0))

        def choose(value: bool) -> None:
            if value and confirm_message:
                confirmed = messagebox.askyesno("Confirm deletion", confirm_message, parent=dialog)
                if not confirmed:
                    return
            result.set(value)
            dialog.destroy()

        ttk.Button(button_row, text="Yes", command=lambda: choose(True), width=12).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(button_row, text="No", command=lambda: choose(False), width=12).pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(False))
        dialog.update_idletasks()
        widest_line = max((len(line) for line in message.splitlines()), default=80)
        screen_width = dialog.winfo_screenwidth()
        width = min(max(520, 70 + widest_line * 9), max(520, screen_width - 80))
        message_label.configure(wraplength=max(width - 60, 460))
        height = max(220, dialog.winfo_reqheight())
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - width) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - height) // 2, 0)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.focus_force()
        self.root.wait_window(dialog)
        return result.get()


def _run_selftest(args: list[str]) -> int:
    """Headless end-to-end check used to verify a packaged build.

    Usage: VideoTranslatorSubtitles.exe --selftest <video> <log file> [srt|burn]
    """
    if len(args) < 2:
        return 2
    video = Path(args[0])
    log_file = Path(args[1])
    mode = args[2].lower() if len(args) > 2 else "burn"
    model_size = os.environ.get("VTS_SELFTEST_MODEL", "tiny")
    lines = [
        f"frozen={getattr(sys, 'frozen', False)}",
        f"video={video}",
        f"mode={mode}",
        f"model={model_size}",
    ]
    root = create_root()
    root.withdraw()
    app = VideoTranslatorApp(root)
    app.persist_settings = False  # a self-test must not rewrite the user's preferences
    app.model_size.set(model_size)
    exit_code = 1
    try:
        ffmpeg_path = app._get_ffmpeg_path()
        lines.append(f"ffmpeg={ffmpeg_path}")
        if ffmpeg_path is None:
            raise RuntimeError("FFmpeg not found inside the packaged app.")
        burn = mode == "burn"
        height = int(app.output_height.get()) if burn and app.reencode_video.get() else None
        lines.append(f"burn={burn} height={height}")
        app._process_batch([video], model_size, burn, height, skip_existing=False)
        results: list[tuple[Path, Path]] = []
        failures: list[tuple[Path, str]] = []
        while True:
            try:
                event, payload = app.message_queue.get_nowait()
            except queue.Empty:
                break
            if event == "status":
                status_text, progress = payload
                lines.append(f"status {float(progress):5.1f}% {str(status_text).splitlines()[-1]}")
            elif event == "batch_done":
                results, failures, _cancelled, _skipped = payload
            elif event == "error":
                failures = [(video, str(payload))]
        for _input_file, output_file in results:
            if output_file.exists():
                lines.append(f"output={output_file} bytes={output_file.stat().st_size}")
            else:
                lines.append(f"output MISSING {output_file}")
        lines.extend(f"failure={error.strip()}" for _input_file, error in failures)
        exit_code = 0 if results and not failures else 1
    except Exception as exc:
        lines.append(f"exception={type(exc).__name__}: {exc}")
    finally:
        log_file.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
        try:
            root.destroy()
        except Exception:
            pass
    return exit_code

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest_args = sys.argv[sys.argv.index("--selftest") + 1:]
        raise SystemExit(_run_selftest(selftest_args))
    root_window = create_root()
    app = VideoTranslatorApp(root_window)
    root_window.mainloop()
