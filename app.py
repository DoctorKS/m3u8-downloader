from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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
CANCELLED_RESULT = "__cancelled__"


@dataclass(frozen=True)
class DownloadJob:
    item_id: str
    audio_url: str
    video_url: str
    output: Path


class M3U8Downloader(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("M3U8 Batch Downloader")
        self.geometry("940x650")
        self.minsize(760, 580)

        self.audio_url = tk.StringVar(value=DEFAULT_AUDIO_URL)
        self.video_url = tk.StringVar(value=DEFAULT_VIDEO_URL)
        self.output_path = tk.StringVar(value=str(Path.home() / "Downloads" / "merged-video.mp4"))
        self.status_text = tk.StringVar(value="พร้อมเพิ่มงานลงคิว")
        self.progress_text = tk.StringVar(value="0%")
        self.queue_summary = tk.StringVar(value="ทั้งหมด 0 รายการ")

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.jobs: dict[str, DownloadJob] = {}
        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.cancel_requested = threading.Event()
        self.current_job_id: str | None = None
        self.text_entries: list[ttk.Entry] = []

        self._build_ui()
        self._install_clipboard_shortcuts()
        self._add_job(silent=True)
        self.after(100, self._handle_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(5, weight=1)

        ttk.Label(outer, text="เพิ่มรายการดาวน์โหลด", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )

        ttk.Label(outer, text="ลิงก์เสียง (.m3u8)").grid(row=1, column=0, sticky="w", pady=5)
        self.audio_entry = ttk.Entry(outer, textvariable=self.audio_url)
        self.audio_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=5)
        self.text_entries.append(self.audio_entry)

        ttk.Label(outer, text="ลิงก์วิดีโอ (.m3u8)").grid(row=2, column=0, sticky="w", pady=5)
        self.video_entry = ttk.Entry(outer, textvariable=self.video_url)
        self.video_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=5)
        self.text_entries.append(self.video_entry)

        ttk.Label(outer, text="บันทึกเป็น").grid(row=3, column=0, sticky="w", pady=5)
        self.output_entry = ttk.Entry(outer, textvariable=self.output_path)
        self.output_entry.grid(row=3, column=1, sticky="ew", padx=(12, 8), pady=5)
        self.text_entries.append(self.output_entry)
        self.browse_button = ttk.Button(outer, text="เลือกไฟล์...", command=self._choose_output)
        self.browse_button.grid(row=3, column=2, sticky="ew", pady=5)

        add_row = ttk.Frame(outer)
        add_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 14))
        add_row.columnconfigure(0, weight=1)
        ttk.Label(add_row, text="แต่ละรายการจะดาวน์โหลดและ merge ทีละคู่ตามลำดับคิว", foreground="#555555").grid(
            row=0, column=0, sticky="w"
        )
        self.add_button = ttk.Button(add_row, text="เพิ่มเข้าคิว", command=self._add_job)
        self.add_button.grid(row=0, column=1, sticky="e")

        queue_frame = ttk.LabelFrame(outer, text=" คิวดาวน์โหลด ", padding=10)
        queue_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)

        columns = ("number", "output", "status", "progress")
        self.queue_tree = ttk.Treeview(queue_frame, columns=columns, show="headings", selectmode="extended")
        self.queue_tree.heading("number", text="#")
        self.queue_tree.heading("output", text="ไฟล์ปลายทาง")
        self.queue_tree.heading("status", text="สถานะ")
        self.queue_tree.heading("progress", text="ความคืบหน้า")
        self.queue_tree.column("number", width=46, minwidth=40, anchor="center", stretch=False)
        self.queue_tree.column("output", width=520, minwidth=260)
        self.queue_tree.column("status", width=130, minwidth=110, anchor="center", stretch=False)
        self.queue_tree.column("progress", width=100, minwidth=90, anchor="center", stretch=False)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.queue_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.queue_tree.configure(yscrollcommand=scrollbar.set)

        queue_buttons = ttk.Frame(queue_frame)
        queue_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        queue_buttons.columnconfigure(0, weight=1)
        ttk.Label(queue_buttons, textvariable=self.queue_summary).grid(row=0, column=0, sticky="w")
        self.remove_button = ttk.Button(queue_buttons, text="ลบรายการที่เลือก", command=self._remove_selected)
        self.remove_button.grid(row=0, column=1, padx=(8, 0))
        self.clear_button = ttk.Button(queue_buttons, text="ล้างคิว", command=self._clear_queue)
        self.clear_button.grid(row=0, column=2, padx=(8, 0))

        progress_frame = ttk.Frame(outer)
        progress_frame.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.progress_text, width=7, anchor="e").grid(
            row=0, column=1, padx=(10, 0)
        )
        ttk.Label(outer, textvariable=self.status_text).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        action_row = ttk.Frame(outer)
        action_row.grid(row=8, column=0, columnspan=3, sticky="e", pady=(16, 0))
        self.cancel_button = ttk.Button(action_row, text="หยุดทั้งหมด", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.download_button = ttk.Button(action_row, text="เริ่มดาวน์โหลดตามคิว", command=self._start_queue)
        self.download_button.pack(side="left")

    def _install_clipboard_shortcuts(self) -> None:
        self.entry_menu = tk.Menu(self, tearoff=False)
        self.entry_menu.add_command(label="Cut", command=lambda: self._entry_event("<<Cut>>"))
        self.entry_menu.add_command(label="Copy", command=lambda: self._entry_event("<<Copy>>"))
        self.entry_menu.add_command(label="Paste", command=lambda: self._entry_event("<<Paste>>"))
        self.entry_menu.add_separator()
        self.entry_menu.add_command(label="Select All", command=self._select_all_focused_entry)

        for entry in self.text_entries:
            entry.bind("<Control-a>", self._select_all)
            entry.bind("<Control-A>", self._select_all)
            entry.bind("<Button-3>", self._show_entry_menu)

        self.queue_tree.bind("<Control-c>", self._copy_selected_queue_rows)
        self.queue_tree.bind("<Control-C>", self._copy_selected_queue_rows)
        self.bind_all("<Control-c>", self._global_copy, add="+")
        self.bind_all("<Control-C>", self._global_copy, add="+")
        self.bind_all("<Control-Insert>", self._global_copy, add="+")

    def _focused_entry(self) -> ttk.Entry | None:
        focused = self.focus_get()
        if focused in self.text_entries:
            return focused  # type: ignore[return-value]
        return None

    def _entry_event(self, virtual_event: str) -> None:
        entry = self._focused_entry()
        if entry is not None:
            entry.event_generate(virtual_event)

    def _entry_is_editable(self, entry: ttk.Entry) -> bool:
        return str(entry.cget("state")) == "normal"

    def _select_all(self, event: tk.Event) -> str:
        widget = event.widget
        if isinstance(widget, ttk.Entry):
            widget.selection_range(0, tk.END)
            widget.icursor(tk.END)
            return "break"
        return ""

    def _select_all_focused_entry(self) -> None:
        entry = self._focused_entry()
        if entry is not None:
            entry.selection_range(0, tk.END)
            entry.icursor(tk.END)

    def _show_entry_menu(self, event: tk.Event) -> str:
        widget = event.widget
        if isinstance(widget, ttk.Entry):
            widget.focus_set()
            editable = self._entry_is_editable(widget)
            self.entry_menu.entryconfigure("Cut", state="normal" if editable else "disabled")
            self.entry_menu.entryconfigure("Paste", state="normal" if editable else "disabled")
            self.entry_menu.tk_popup(event.x_root, event.y_root)
            return "break"
        return ""

    def _global_copy(self, event: tk.Event) -> str:
        entry = self._focused_entry()
        if entry is not None:
            entry.event_generate("<<Copy>>")
            return "break"
        if self.focus_get() == self.queue_tree:
            return self._copy_selected_queue_rows(event)
        return ""

    def _copy_selected_queue_rows(self, event: tk.Event | None = None) -> str:
        selected = self.queue_tree.selection()
        if not selected:
            return "break"
        rows = []
        for item_id in selected:
            values = self.queue_tree.item(item_id, "values")
            rows.append("\t".join(str(value) for value in values))
        self.clipboard_clear()
        self.clipboard_append("\n".join(rows))
        return "break"

    def _choose_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="เลือกตำแหน่งบันทึก",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
            initialfile=Path(self.output_path.get()).name,
        )
        if selected:
            self.output_path.set(selected)
            self.output_entry.focus_set()
            self.output_entry.icursor(tk.END)

    @staticmethod
    def _valid_url(value: str) -> bool:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and parsed.path.lower().endswith(".m3u8")

    def _add_job(self, silent: bool = False) -> bool:
        audio_url = self.audio_url.get().strip()
        video_url = self.video_url.get().strip()
        if not self._valid_url(audio_url) or not self._valid_url(video_url):
            if not silent:
                messagebox.showwarning("ลิงก์ไม่ถูกต้อง", "กรุณากรอกลิงก์ http/https ที่ลงท้ายด้วย .m3u8 ทั้งสองช่อง")
            return False

        raw_output = self.output_path.get().strip()
        if not raw_output:
            if not silent:
                messagebox.showwarning("ไม่ได้เลือกไฟล์", "กรุณาระบุไฟล์ปลายทาง")
            return False
        output = Path(raw_output).expanduser()
        if output.suffix.lower() != ".mp4":
            output = output.with_suffix(".mp4")
            self.output_path.set(str(output))
        if any(job.output.resolve() == output.resolve() for job in self.jobs.values()):
            if not silent:
                messagebox.showwarning("ไฟล์ซ้ำ", "ไฟล์ปลายทางนี้มีอยู่ในคิวแล้ว กรุณาเปลี่ยนชื่อไฟล์")
            return False

        item_id = self.queue_tree.insert(
            "", "end", values=(len(self.jobs) + 1, str(output), "รอคิว", "0%")
        )
        self.jobs[item_id] = DownloadJob(item_id, audio_url, video_url, output)
        self._renumber_queue()
        self._suggest_next_output(output)
        self.status_text.set("เพิ่มรายการเข้าคิวแล้ว")
        return True

    def _suggest_next_output(self, previous: Path) -> None:
        number = len(self.jobs) + 1
        stem_parts = previous.stem.rsplit("-", 1)
        base_stem = stem_parts[0] if len(stem_parts) == 2 and stem_parts[1].isdigit() else previous.stem
        while True:
            candidate = previous.with_name(f"{base_stem}-{number}{previous.suffix}")
            if not any(job.output.resolve() == candidate.resolve() for job in self.jobs.values()):
                self.output_path.set(str(candidate))
                return
            number += 1

    def _renumber_queue(self) -> None:
        for number, item_id in enumerate(self.queue_tree.get_children(), start=1):
            values = list(self.queue_tree.item(item_id, "values"))
            values[0] = number
            self.queue_tree.item(item_id, values=values)
        self.queue_summary.set(f"ทั้งหมด {len(self.jobs)} รายการ")

    def _remove_selected(self) -> None:
        for item_id in self.queue_tree.selection():
            self.jobs.pop(item_id, None)
            self.queue_tree.delete(item_id)
        self._renumber_queue()

    def _clear_queue(self) -> None:
        if self.jobs and not messagebox.askyesno("ยืนยัน", "ต้องการล้างรายการทั้งหมดในคิวหรือไม่?"):
            return
        for item_id in self.queue_tree.get_children():
            self.queue_tree.delete(item_id)
        self.jobs.clear()
        self._renumber_queue()
        self.progress.configure(value=0)
        self.progress_text.set("0%")
        self.status_text.set("คิวว่าง")

    def _start_queue(self) -> None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror(
                "ไม่พบ ffmpeg/ffprobe",
                "กรุณาติดตั้ง ffmpeg (ซึ่งรวม ffprobe) และเพิ่มโฟลเดอร์ bin ลงใน PATH แล้วเปิดโปรแกรมใหม่\n\n"
                "Windows: winget install Gyan.FFmpeg",
            )
            return

        pending_ids = [
            item_id for item_id in self.queue_tree.get_children()
            if self.queue_tree.set(item_id, "status") in {"รอคิว", "ล้มเหลว", "ยกเลิก"}
        ]
        if not pending_ids:
            messagebox.showinfo("ไม่มีงาน", "กรุณาเพิ่มรายการใหม่ลงคิว")
            return

        existing = [self.jobs[item_id].output for item_id in pending_ids if self.jobs[item_id].output.exists()]
        if existing and not messagebox.askyesno(
            "ไฟล์มีอยู่แล้ว",
            f"พบไฟล์ปลายทางที่มีอยู่แล้ว {len(existing)} ไฟล์ ต้องการเขียนทับทั้งหมดหรือไม่?",
        ):
            return

        for item_id in pending_ids:
            self._update_job_row(item_id, "รอคิว", 0.0)
        batch = [self.jobs[item_id] for item_id in pending_ids]
        self.cancel_requested.clear()
        self._set_running(True)
        self.worker = threading.Thread(target=self._run_queue, args=(batch,), daemon=True)
        self.worker.start()

    def _run_queue(self, batch: list[DownloadJob]) -> None:
        succeeded = 0
        failed = 0
        errors: list[str] = []
        total = len(batch)
        for index, job in enumerate(batch, start=1):
            if self.cancel_requested.is_set():
                self.events.put(("job_status", {"id": job.item_id, "status": "ยกเลิก", "percent": 0.0}))
                continue
            self.current_job_id = job.item_id
            self.events.put(("job_started", {"id": job.item_id, "index": index, "total": total}))
            error = self._run_ffmpeg(job, index, total)
            if error == CANCELLED_RESULT:
                self.events.put(("job_status", {"id": job.item_id, "status": "ยกเลิก", "percent": 0.0}))
            elif error is None:
                succeeded += 1
                self.events.put(("job_status", {"id": job.item_id, "status": "สำเร็จ", "percent": 100.0}))
            else:
                failed += 1
                errors.append(f"{job.output.name}: {error}")
                self.events.put(("job_status", {"id": job.item_id, "status": "ล้มเหลว", "percent": 0.0}))
        self.current_job_id = None
        self.events.put(("batch_done", {"succeeded": succeeded, "failed": failed, "errors": errors, "cancelled": self.cancel_requested.is_set()}))

    def _run_ffmpeg(self, job: DownloadJob, index: int, total: int) -> str | None:
        job.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = job.output.with_name(f"{job.output.stem}.part{job.output.suffix}")
        temporary.unlink(missing_ok=True)
        self.events.put(("status", f"รายการ {index}/{total}: กำลังตรวจสอบระยะเวลา..."))
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                durations = list(executor.map(self._probe_duration, (job.video_url, job.audio_url)))
            total_duration = min(durations)
        except Exception as exc:
            return f"ไม่สามารถอ่านระยะเวลาของสตรีมได้: {exc}"

        if self.cancel_requested.is_set():
            return CANCELLED_RESULT

        self.events.put(("progress", {"id": job.item_id, "percent": 0.0, "remaining": total_duration, "index": index, "total": total}))
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-stats_period", "1", "-progress", "pipe:1", "-y",
            "-i", job.video_url, "-i", job.audio_url,
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
            "-shortest", "-movflags", "+faststart", str(temporary),
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", creationflags=flags,
            )
            assert self.process.stdout is not None
            processed_seconds = 0.0
            current_speed = 1.0
            for line in self.process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit():
                    processed_seconds = int(value) / 1_000_000
                elif key == "speed" and value.endswith("x"):
                    try:
                        current_speed = max(float(value[:-1]), 0.01)
                    except ValueError:
                        current_speed = 1.0
                elif key == "progress":
                    percent = min(processed_seconds / total_duration * 100, 99.9)
                    remaining = max(total_duration - processed_seconds, 0) / current_speed
                    self.events.put(("progress", {"id": job.item_id, "percent": percent, "remaining": remaining, "index": index, "total": total}))

            stderr = self.process.stderr.read() if self.process.stderr else ""
            return_code = self.process.wait()
            if return_code == 0:
                os.replace(temporary, job.output)
                return None
            temporary.unlink(missing_ok=True)
            if self.cancel_requested.is_set():
                return CANCELLED_RESULT
            return stderr.strip()[-1200:] or f"ffmpeg จบการทำงานด้วยรหัส {return_code}"
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if self.cancel_requested.is_set():
                return CANCELLED_RESULT
            return str(exc)
        finally:
            self.process = None

    @staticmethod
    def _probe_duration(url: str) -> float:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=flags, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffprobe อ่านสตรีมไม่สำเร็จ")
        try:
            duration = float(result.stdout.strip())
        except ValueError as exc:
            raise RuntimeError("สตรีมไม่แจ้งระยะเวลาที่ถูกต้อง") from exc
        if duration <= 0:
            raise RuntimeError("ระยะเวลาของสตรีมต้องมากกว่า 0")
        return duration

    @staticmethod
    def _format_remaining(seconds: float) -> str:
        seconds = max(int(round(seconds)), 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def _update_job_row(self, item_id: str, status: str, percent: float) -> None:
        if not self.queue_tree.exists(item_id):
            return
        values = list(self.queue_tree.item(item_id, "values"))
        values[2] = status
        values[3] = f"{percent:.1f}%"
        self.queue_tree.item(item_id, values=values)
        self.queue_tree.see(item_id)

    def _cancel(self) -> None:
        self.cancel_requested.set()
        if self.process and self.process.poll() is None:
            self.process.kill()
        self.status_text.set("กำลังหยุดงานปัจจุบันและยกเลิกคิวที่เหลือ...")
        self.cancel_button.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        entry_state = "readonly" if running else "normal"
        for entry in self.text_entries:
            entry.configure(state=entry_state)
        button_state = "disabled" if running else "normal"
        for widget in (self.browse_button, self.add_button, self.remove_button, self.clear_button, self.download_button):
            widget.configure(state=button_state)
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.configure(value=0)
            self.progress_text.set("0%")
            self.status_text.set("กำลังเริ่มประมวลผลคิว...")

    def _handle_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                data = value if isinstance(value, dict) else {}
                if event == "status":
                    self.status_text.set(str(value))
                elif event == "job_started":
                    item_id = str(data["id"])
                    self._update_job_row(item_id, "กำลังทำงาน", 0.0)
                    self.queue_tree.selection_set(item_id)
                elif event == "progress":
                    item_id = str(data["id"])
                    percent = float(data["percent"])
                    remaining = float(data["remaining"])
                    self.progress.configure(value=percent)
                    self.progress_text.set(f"{percent:.1f}%")
                    self._update_job_row(item_id, "กำลังทำงาน", percent)
                    self.status_text.set(
                        f"รายการ {data['index']}/{data['total']}: ดาวน์โหลดและ merge — เหลือประมาณ {self._format_remaining(remaining)}"
                    )
                elif event == "job_status":
                    self._update_job_row(str(data["id"]), str(data["status"]), float(data["percent"]))
                elif event == "batch_done":
                    self._set_running(False)
                    succeeded = int(data["succeeded"])
                    failed = int(data["failed"])
                    if data["cancelled"]:
                        self.status_text.set(f"หยุดแล้ว — สำเร็จ {succeeded}, ล้มเหลว {failed}")
                    else:
                        self.progress.configure(value=100 if failed == 0 else self.progress["value"])
                        self.progress_text.set("100%" if failed == 0 else self.progress_text.get())
                        self.status_text.set(f"เสร็จสิ้น — สำเร็จ {succeeded}, ล้มเหลว {failed}")
                    details = "\n\n".join(data["errors"][:5])
                    message = f"ประมวลผลคิวเสร็จแล้ว\nสำเร็จ: {succeeded}\nล้มเหลว: {failed}"
                    if details:
                        message += f"\n\nรายละเอียด:\n{details}"
                    messagebox.showinfo("สรุป Batch", message)
        except queue.Empty:
            pass
        self.after(100, self._handle_events)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("ยืนยัน", "คิวยังทำงานอยู่ ต้องการหยุดและปิดโปรแกรมหรือไม่?"):
                return
            self.cancel_requested.set()
            if self.process and self.process.poll() is None:
                self.process.kill()
        self.destroy()


if __name__ == "__main__":
    M3U8Downloader().mainloop()
