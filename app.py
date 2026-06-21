from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlparse


DEFAULT_AUDIO_URL = (
    "https://academy-asia.imcascdn.com/hls/43203/"
    "6a3553f27c4cf6a3553f27c4cfsound-en.m3u8"
)
DEFAULT_VIDEO_URL = (
    "https://academy-asia.imcascdn.com/hls/43203/"
    "6a3553f27c4cf6a3553f27c4cfhd.m3u8"
)


class M3U8Downloader(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("M3U8 Video + Audio Downloader")
        self.geometry("720x390")
        self.minsize(620, 360)

        self.audio_url = tk.StringVar(value=DEFAULT_AUDIO_URL)
        self.video_url = tk.StringVar(value=DEFAULT_VIDEO_URL)
        self.output_path = tk.StringVar(value=str(Path.home() / "Downloads" / "merged-video.mp4"))
        self.status_text = tk.StringVar(value="พร้อมดาวน์โหลด")
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.after(100, self._handle_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="ลิงก์เสียง (.m3u8)").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.audio_entry = ttk.Entry(outer, textvariable=self.audio_url)
        self.audio_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=(0, 8))

        ttk.Label(outer, text="ลิงก์วิดีโอ (.m3u8)").grid(row=1, column=0, sticky="w", pady=8)
        self.video_entry = ttk.Entry(outer, textvariable=self.video_url)
        self.video_entry.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=8)

        ttk.Label(outer, text="บันทึกเป็น").grid(row=2, column=0, sticky="w", pady=8)
        output_frame = ttk.Frame(outer)
        output_frame.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=8)
        output_frame.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(output_frame, textvariable=self.output_path)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(output_frame, text="เลือกไฟล์...", command=self._choose_output)
        self.browse_button.grid(row=0, column=1, padx=(8, 0))

        ttk.Separator(outer).grid(row=3, column=0, columnspan=2, sticky="ew", pady=18)

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Label(outer, textvariable=self.status_text).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        buttons = ttk.Frame(outer)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(22, 0))
        self.cancel_button = ttk.Button(buttons, text="ยกเลิก", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.download_button = ttk.Button(buttons, text="ดาวน์โหลดและรวมไฟล์", command=self._start)
        self.download_button.pack(side="left")

        ttk.Label(
            outer,
            text="โปรแกรมใช้ ffmpeg อ่านเสียงและวิดีโอพร้อมกัน แล้วรวมเป็น MP4 โดยไม่ลดคุณภาพ",
            foreground="#555555",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(18, 0))

    def _choose_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="เลือกตำแหน่งบันทึก",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
            initialfile=Path(self.output_path.get()).name,
        )
        if selected:
            self.output_path.set(selected)

    @staticmethod
    def _valid_url(value: str) -> bool:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and parsed.path.lower().endswith(".m3u8")

    def _start(self) -> None:
        if not shutil.which("ffmpeg"):
            messagebox.showerror(
                "ไม่พบ ffmpeg",
                "กรุณาติดตั้ง ffmpeg และเพิ่มโฟลเดอร์ bin ลงใน PATH แล้วเปิดโปรแกรมใหม่\n\n"
                "Windows: winget install Gyan.FFmpeg",
            )
            return

        audio_url = self.audio_url.get().strip()
        video_url = self.video_url.get().strip()
        if not self._valid_url(audio_url) or not self._valid_url(video_url):
            messagebox.showwarning("ลิงก์ไม่ถูกต้อง", "กรุณากรอกลิงก์ http/https ที่ลงท้ายด้วย .m3u8 ทั้งสองช่อง")
            return

        output = Path(self.output_path.get().strip()).expanduser()
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
            self.output_path.set(str(output))
        if output.exists() and not messagebox.askyesno("ไฟล์มีอยู่แล้ว", f"ต้องการเขียนทับไฟล์นี้หรือไม่?\n{output}"):
            return

        output.parent.mkdir(parents=True, exist_ok=True)
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_ffmpeg, args=(video_url, audio_url, output), daemon=True
        )
        self.worker.start()

    def _run_ffmpeg(self, video_url: str, audio_url: str, output: Path) -> None:
        temporary = output.with_name(f"{output.stem}.part{output.suffix}")
        temporary.unlink(missing_ok=True)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-stats_period", "1",
            "-progress", "pipe:1",
            "-y",
            "-i", video_url,
            "-i", audio_url,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c", "copy",
            "-shortest",
            "-movflags", "+faststart",
            str(temporary),
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time" and value:
                    self.events.put(("status", f"กำลังดาวน์โหลดและรวมไฟล์... เวลาวิดีโอ {value[:8]}"))

            stderr = self.process.stderr.read() if self.process.stderr else ""
            return_code = self.process.wait()
            if return_code == 0:
                os.replace(temporary, output)
                self.events.put(("done", str(output)))
            elif return_code != -1:
                temporary.unlink(missing_ok=True)
                detail = stderr.strip()[-1500:] or f"ffmpeg จบการทำงานด้วยรหัส {return_code}"
                self.events.put(("error", detail))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self.events.put(("error", str(exc)))
        finally:
            self.process = None

    def _cancel(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
        self.events.put(("cancelled", "ยกเลิกแล้ว"))

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in (self.audio_entry, self.video_entry, self.output_entry, self.browse_button):
            widget.configure(state=state)
        self.download_button.configure(state=state)
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.status_text.set("กำลังเชื่อมต่อและดาวน์โหลดทั้งสองลิงก์...")
            self.progress.start(12)
        else:
            self.progress.stop()

    def _handle_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "status":
                    self.status_text.set(value)
                elif event == "done":
                    self._set_running(False)
                    self.status_text.set("ดาวน์โหลดและรวมไฟล์สำเร็จ")
                    messagebox.showinfo("สำเร็จ", f"บันทึกไฟล์แล้ว:\n{value}")
                elif event == "error":
                    self._set_running(False)
                    self.status_text.set("เกิดข้อผิดพลาด")
                    messagebox.showerror("ดาวน์โหลดไม่สำเร็จ", value)
                elif event == "cancelled":
                    self._set_running(False)
                    self.status_text.set(value)
        except queue.Empty:
            pass
        self.after(100, self._handle_events)

    def _on_close(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("ยืนยัน", "กำลังดาวน์โหลดอยู่ ต้องการปิดโปรแกรมหรือไม่?"):
                return
            self.process.kill()
        self.destroy()


if __name__ == "__main__":
    M3U8Downloader().mainloop()
