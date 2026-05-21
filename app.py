import os
import queue
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

os.environ.setdefault("HF_HOME", str(Path.home() / "AppData" / "Local" / "VideoTranslatorSubtitles" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import imageio_ffmpeg
from faster_whisper import WhisperModel


@dataclass
class TranslatedSegment:
    index: int
    start: float
    end: float
    text: str


class VideoTranslatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MP4 Speech Translator to English Subtitles")
        self.root.geometry("720x460")
        self.root.minsize(680, 420)

        self.input_path = tk.StringVar()
        self.model_size = tk.StringVar(value="base")
        self.embed_subtitles = tk.BooleanVar(value=False)
        self.reencode_720p = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Choose an MP4 file to begin.")
        self.progress_value = tk.DoubleVar(value=0)
        self.message_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self._build_ui()
        self.root.after(100, self._process_queue)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(frame, text="MP4 Speech Translator to English Subtitles", font=("Segoe UI", 18, "bold"))
        title.pack(anchor=tk.W)

        subtitle = ttk.Label(
            frame,
            text="Open an MP4 and translate the spoken language to an English .srt subtitle file. Optionally embed subtitles into a new _eng MP4.",
            wraplength=660,
        )
        subtitle.pack(anchor=tk.W, pady=(4, 18))

        input_row = ttk.Frame(frame)
        input_row.pack(fill=tk.X, pady=6)
        ttk.Label(input_row, text="Input MP4", width=14).pack(side=tk.LEFT)
        ttk.Entry(input_row, textvariable=self.input_path).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(input_row, text="Browse", command=self._choose_input).pack(side=tk.LEFT)

        model_row = ttk.Frame(frame)
        model_row.pack(fill=tk.X, pady=6)
        ttk.Label(model_row, text="Whisper model", width=14).pack(side=tk.LEFT)
        model_box = ttk.Combobox(
            model_row,
            textvariable=self.model_size,
            values=("tiny", "base", "small", "medium", "large-v3"),
            state="readonly",
            width=18,
        )
        model_box.pack(side=tk.LEFT, padx=8)
        ttk.Label(model_row, text="Use tiny/base for faster processing. Larger models are much slower on CPU.").pack(side=tk.LEFT)

        embed_row = ttk.Frame(frame)
        embed_row.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            embed_row,
            text='Burn subtitles visibly into a new video ending with "_eng.mp4"',
            variable=self.embed_subtitles,
        ).pack(anchor=tk.W)

        reencode_row = ttk.Frame(frame)
        reencode_row.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            reencode_row,
            text="Re-encode output video to 720p",
            variable=self.reencode_720p,
        ).pack(anchor=tk.W)

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(18, 8))
        self.convert_button = ttk.Button(actions, text="Create Subtitle File", command=self._start_conversion)
        self.convert_button.pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(frame, variable=self.progress_value, maximum=100)
        self.progress.pack(fill=tk.X, pady=(12, 8))

        status_box = ttk.LabelFrame(frame, text="Status", padding=10)
        status_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.status_label = ttk.Label(status_box, textvariable=self.status_text, wraplength=640, justify=tk.LEFT)
        self.status_label.pack(anchor=tk.NW, fill=tk.BOTH, expand=True)

    def _choose_input(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")])
        if not selected:
            return
        self.input_path.set(selected)

    def _start_conversion(self) -> None:
        input_file = Path(self.input_path.get())

        if not input_file.exists() or input_file.suffix.lower() != ".mp4":
            messagebox.showerror("Invalid input", "Please choose a valid MP4 file.")
            return
        if self._get_ffmpeg_path() is None:
            messagebox.showerror("FFmpeg not found", "FFmpeg is not available. Rebuild the app or install FFmpeg.")
            return

        self.convert_button.configure(state=tk.DISABLED)
        self.progress_value.set(0)
        self.status_text.set("Starting conversion...")
        self.worker_thread = threading.Thread(
            target=self._convert_video,
            args=(input_file, self.model_size.get(), self.embed_subtitles.get(), self.reencode_720p.get()),
            daemon=True,
        )
        self.worker_thread.start()

    def _convert_video(self, input_file: Path, model_size: str, embed_subtitles: bool, reencode_720p: bool) -> None:
        try:
            with tempfile.TemporaryDirectory() as temp_dir_name:
                temp_dir = Path(temp_dir_name)
                source_audio = temp_dir / "source.wav"

                self._post_status("Extracting audio from MP4...", 8)
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

                self._post_status(f"Loading Whisper model '{model_size}'...", 18)
                model = WhisperModel(model_size, device="cpu", compute_type="int8")

                self._post_status("Translating speech to English text. This can take several minutes on CPU...", 25)
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
                    progress = 25 + min(raw_segment.end / duration, 1.0) * 30
                    self._post_status(
                        f"Translating speech to English text...\nProcessed {raw_segment.end:.1f} of {duration:.1f} seconds.",
                        progress,
                    )

                if not segments:
                    raise RuntimeError("No speech was detected in the video.")

                self._post_status("Saving English subtitles...", 80)
                srt_file = input_file.with_suffix(".srt")
                srt_content = self._segments_to_srt(segments)
                srt_file.write_text(srt_content, encoding="utf-8")

                embedded_video = None
                if embed_subtitles:
                    embedded_video = input_file.with_name(f"{input_file.stem}_eng{input_file.suffix}")
                    output_description = " at 720p" if reencode_720p else ""
                    self._post_status(f"Burning subtitles into new MP4{output_description}. This may take a while...", 85)
                    temp_embed_srt = temp_dir / "english.srt"
                    temp_embed_srt.write_text(srt_content, encoding="utf-8")
                    self._embed_subtitles(input_file, temp_embed_srt, embedded_video, reencode_720p)
                    srt_file.unlink(missing_ok=True)

            if embed_subtitles and embedded_video:
                done_message = f"Saved subtitled video:\n{embedded_video}\n\nDeleted temporary subtitle file:\n{input_file.with_suffix('.srt')}"
            else:
                done_message = f"Saved English subtitles:\n{input_file.with_suffix('.srt')}"
            self.message_queue.put(("done", done_message))
        except Exception as exc:
            self.message_queue.put(("error", str(exc)))

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

    def _embed_subtitles(self, input_file: Path, srt_file: Path, output_file: Path, reencode_720p: bool) -> None:
        subtitle_filter = f"subtitles={self._format_ffmpeg_filter_path(srt_file)}"
        video_filter = subtitle_filter
        if reencode_720p:
            video_filter = f"scale=-2:720,{subtitle_filter}"
        self._run_ffmpeg([
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
            str(output_file),
        ])
        self._post_status("Finished burning subtitles.", 98)

    def _format_ffmpeg_filter_path(self, path: Path) -> str:
        formatted = path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
        return f"'{formatted}'"

    def _run_ffmpeg(self, command: list[str]) -> None:
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 6
            creationflags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            command_text = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command)
            error_text = result.stderr.strip() or result.stdout.strip() or "FFmpeg failed."
            raise RuntimeError(f"FFmpeg failed while opening or writing a file.\n\nCommand:\n{command_text}\n\nDetails:\n{error_text}")

    def _get_ffmpeg_path(self) -> str | None:
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return system_ffmpeg
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    def _post_status(self, text: str, progress: float) -> None:
        self.message_queue.put(("status", (text, progress)))

    def _process_queue(self) -> None:
        try:
            while True:
                event, payload = self.message_queue.get_nowait()
                if event == "status":
                    text, progress = payload
                    self.status_text.set(str(text))
                    self.progress_value.set(float(progress))
                elif event == "done":
                    self.progress_value.set(100)
                    self.status_text.set(str(payload))
                    self.convert_button.configure(state=tk.NORMAL)
                    messagebox.showinfo("Conversion complete", str(payload))
                elif event == "error":
                    self.status_text.set(f"Error: {payload}")
                    self.convert_button.configure(state=tk.NORMAL)
                    messagebox.showerror("Conversion failed", str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)


if __name__ == "__main__":
    root_window = tk.Tk()
    app = VideoTranslatorApp(root_window)
    root_window.mainloop()
