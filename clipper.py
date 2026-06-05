import subprocess
import os
import uuid
import threading
import re
import sys

# Shared progress state across tasks
_tasks = {}
_lock = threading.Lock()


def create_task() -> str:
    """Create a new task and return its ID."""
    task_id = str(uuid.uuid4())
    with _lock:
        _tasks[task_id] = {
            "status": "pending",      # pending | downloading | subtitles | cutting | embedding | done | error
            "progress": 0,
            "logs": [],
            "output_file": None,
            "error": None,
        }
    return task_id


def get_task(task_id: str) -> dict | None:
    with _lock:
        return _tasks.get(task_id)


def get_tasks_batch(task_ids: list) -> dict:
    """Return a dict of {task_id: task_data} for all given task IDs."""
    with _lock:
        return {tid: _tasks.get(tid) for tid in task_ids if tid in _tasks}


def _update_task(task_id: str, **kwargs):
    with _lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


def _append_log(task_id: str, message: str):
    with _lock:
        if task_id in _tasks:
            _tasks[task_id]["logs"].append(message)


def _seconds_to_ffmpeg(ts: str) -> str:
    """
    Accept HH:MM:SS, MM:SS, or raw seconds string.
    Returns the string as-is if already valid for FFmpeg.
    """
    ts = ts.strip()
    if re.fullmatch(r"[\d.]+", ts):
        return ts
    return ts


def _parse_seconds(ts: str) -> float:
    """Convert HH:MM:SS or MM:SS or raw seconds string to float seconds."""
    ts = ts.strip()
    if re.fullmatch(r"[\d.]+", ts):
        return float(ts)
    parts = ts.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    raise ValueError(f"Format waktu tidak dikenal: {ts}")


def _find_subtitle_file(output_dir: str, task_id: str, lang: str) -> str | None:
    """
    Find the downloaded subtitle file in output_dir.
    yt-dlp names subtitles like: _tmp_<id>.<lang>.srt or _tmp_<id>.srt
    """
    prefix = f"_tmp_{task_id}"
    candidates = []
    for f in os.listdir(output_dir):
        if f.startswith(prefix) and f.endswith(".srt"):
            candidates.append(os.path.join(output_dir, f))
    if not candidates:
        return None
    # Prefer file matching requested language
    for c in candidates:
        if f".{lang}." in os.path.basename(c):
            return c
    return candidates[0]


def _shift_srt(input_path: str, offset_sec: float, output_path: str, text_case: str = "normal"):
    """
    Parse an SRT file, shift all timestamps back by `offset_sec`,
    and drop any lines that fall before 00:00:00.000.
    If text_case is "upper", convert subtitle text to uppercase.
    """
    def _srt_ts_to_ms(ts: str) -> int:
        """HH:MM:SS,mmm -> milliseconds"""
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)

    def _ms_to_srt_ts(ms: int) -> str:
        if ms < 0:
            ms = 0
        h  = ms // 3600000;  ms %= 3600000
        m  = ms // 60000;    ms %= 60000
        s  = ms // 1000;     ms %= 1000
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    offset_ms = int(offset_sec * 1000)

    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Split into subtitle blocks
    blocks = re.split(r"\n\n+", content.strip())
    result_blocks = []
    new_index = 1

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue

        # Find the timecode line  e.g. "00:01:30,000 --> 00:01:35,500"
        tc_line_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                tc_line_idx = i
                break
        if tc_line_idx is None:
            continue

        tc_line = lines[tc_line_idx]
        m = re.match(
            r"([\d:,]+)\s*-->\s*([\d:,]+)(.*)",
            tc_line,
        )
        if not m:
            continue

        start_ms = _srt_ts_to_ms(m.group(1)) - offset_ms
        end_ms   = _srt_ts_to_ms(m.group(2)) - offset_ms

        # Skip entries that end before t=0 (outside the clip)
        if end_ms < 0:
            continue

        new_tc = f"{_ms_to_srt_ts(max(start_ms, 0))} --> {_ms_to_srt_ts(end_ms)}{m.group(3)}"
        text_lines = lines[tc_line_idx + 1:]
        
        # Apply uppercase if requested
        if text_case == "upper":
            text_lines = [line.upper() for line in text_lines]
            
        result_blocks.append(
            f"{new_index}\n{new_tc}\n" + "\n".join(text_lines)
        )
        new_index += 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(result_blocks) + "\n")


def _rgb_to_ass(rgb_hex: str, alpha_hex: str = "00") -> str:
    """Convert RRGGBB hex string to ASS colour &HAABBGGRR (alpha 00=fully opaque, FF=fully transparent)."""
    h = rgb_hex.lstrip("#").upper().zfill(6)
    return f"&H{alpha_hex.upper()}{h[4:6]}{h[2:4]}{h[0:2]}"


def run_clip(
    task_id: str,
    url: str,
    start: str,
    end: str,
    output_dir: str,
    subtitle_enabled: bool = False,
    subtitle_lang: str = "id,en",
    subtitle_type: str = "soft",
    subtitle_auto: bool = True,
    subtitle_position: str = "bottom",
    sub_fontsize: str = "20",
    sub_case: str = "normal",
    sub_bold: bool = False,
    sub_italic: bool = False,
    sub_underline: bool = False,
    video_format: str = "original",
    sub_primary_color: str = "FFFFFF",
    sub_outline_color: str = "000000",
    sub_back_color: str = "000000",
    sub_back_alpha: str = "80",
    sub_border_style: str = "1",
    sub_outline_width: str = "2",
    sub_shadow: str = "1",
    hook_title: str = "",
    hook_fontsize: str = "34",
    hook_preset: str = "yellow-pop",
    cookies: str = "",
):
    """
    Full pipeline: download → (download subtitles) → cut → (embed subtitles) → cleanup.
    Runs in a background thread.
    """
    os.makedirs(output_dir, exist_ok=True)

    temp_path        = os.path.join(output_dir, f"_tmp_{task_id}.%(ext)s")
    output_filename  = f"clip_{task_id}.mp4"
    output_path      = os.path.join(output_dir, output_filename)
    temp_cut_path    = os.path.join(output_dir, f"_tmpcut_{task_id}.mp4")
    temp_hook_sub_path = os.path.join(output_dir, f"_hook_{task_id}.srt")

    try:
        # ── Step 1: Download video (+ optionally subtitles) ─────────────────
        _update_task(task_id, status="downloading", progress=5)
        _append_log(task_id, "[>>] Memulai unduhan video...")

        yt_dlp_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--output", temp_path,
            "--no-playlist",
            "--progress",
            "--newline",
            "--extractor-args", "youtube:player_client=android,web",
        ]

        if cookies:
            yt_dlp_cmd += ["--cookies", cookies]

        if subtitle_enabled:
            first_lang = subtitle_lang.split(",")[0].strip()
            yt_dlp_cmd += [
                "--write-sub",
                "--sub-lang", subtitle_lang,
                "--convert-subs", "srt",
            ]
            if subtitle_auto:
                yt_dlp_cmd.append("--write-auto-sub")
            _append_log(task_id, f"[CC] Subtitle diaktifkan — bahasa: {subtitle_lang}")

        yt_dlp_cmd.append(url)

        downloaded_file = None

        with subprocess.Popen(
            yt_dlp_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as proc:
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                _append_log(task_id, line)

                # Parse yt-dlp progress
                m = re.search(r"\[download\]\s+([\d.]+)%", line)
                if m:
                    pct = float(m.group(1))
                    _update_task(task_id, progress=int(5 + pct * 0.55))

                # Detect final merged/destination video file (not subtitle)
                dest_match = re.search(
                    r"\[(?:download|Merger|ffmpeg)\] Destination:\s*(.+)", line
                )
                if dest_match:
                    candidate = dest_match.group(1).strip()
                    if not candidate.endswith(".srt") and not candidate.endswith(".vtt"):
                        downloaded_file = candidate

        # Resolve actual temp file path
        if not downloaded_file or not os.path.isfile(downloaded_file):
            candidates = [
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if f.startswith(f"_tmp_{task_id}")
                and not f.endswith(".part")
                and not f.endswith(".srt")
                and not f.endswith(".vtt")
            ]
            if candidates:
                downloaded_file = candidates[0]
            else:
                downloaded_file = None

        if not downloaded_file:
            raise RuntimeError(f"Gagal mengunduh video (Exit Code: {proc.returncode}). YouTube mungkin memblokir akses atau URL tidak valid.")

        if proc.returncode != 0:
            _append_log(task_id, "⚠️ Peringatan: yt-dlp melaporkan error (mungkin gagal ambil subtitle akibat limit), tetapi video berhasil diunduh. Melanjutkan proses...")

        _append_log(task_id, f"[OK] Unduhan selesai: {os.path.basename(downloaded_file)}")

        # ── Step 2: Find & process subtitle file ────────────────────────────
        subtitle_file   = None
        shifted_sub_path = None

        if subtitle_enabled:
            _update_task(task_id, status="subtitles", progress=62)
            _append_log(task_id, "[CC] Mencari file subtitle yang diunduh...")

            first_lang = subtitle_lang.split(",")[0].strip()
            subtitle_file = _find_subtitle_file(output_dir, task_id, first_lang)

            if subtitle_file:
                _append_log(task_id, f"[CC] Ditemukan: {os.path.basename(subtitle_file)}")
                # Shift subtitle timestamps to match clip start
                shifted_sub_path = os.path.join(output_dir, f"_sub_{task_id}.srt")
                start_sec = _parse_seconds(start)
                _shift_srt(subtitle_file, start_sec, shifted_sub_path, sub_case)
                _append_log(task_id, "[CC] Timestamp subtitle disesuaikan dengan waktu potong.")
            else:
                _append_log(task_id, "[!] Subtitle tidak ditemukan — lanjut tanpa subtitle.")
                subtitle_enabled = False

        # ── Step 3: Fast Cut Video ───────────────────────────────────────────
        _update_task(task_id, status="cutting", progress=65)
        _append_log(task_id, f"[CUT] Memotong video dari {start} hingga {end}...")

        start_ff = _seconds_to_ffmpeg(start)
        
        try:
            duration = _parse_seconds(end) - _parse_seconds(start)
            duration_ff = str(max(duration, 1.0))
        except Exception:
            duration_ff = _seconds_to_ffmpeg(end) # Fallback

        ffmpeg_cut_cmd = [
            "ffmpeg", "-y",
            "-ss", start_ff,
            "-i", downloaded_file,
            "-t", duration_ff,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            temp_cut_path,
        ]

        _run_ffmpeg(task_id, ffmpeg_cut_cmd, start=65, end=75, start_str=start, end_str=end)

        _update_task(task_id, status="processing", progress=76)
        
        has_hook_title = bool(hook_title)
        if has_hook_title:
            _append_log(task_id, f"[HOOK] Membuat judul hook: {hook_title}")
            with open(temp_hook_sub_path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:04,000\n" + hook_title + "\n")

        needs_reencode = video_format != "original" or (subtitle_enabled and subtitle_type == "burn") or has_hook_title
        has_sub_file = subtitle_enabled and shifted_sub_path and os.path.isfile(shifted_sub_path)

        if not needs_reencode:
            if has_sub_file: # Soft sub only
                _append_log(task_id, "[CC] Menyisipkan subtitle (soft)...")
                ffmpeg_embed_cmd = [
                    "ffmpeg", "-y",
                    "-i", temp_cut_path,
                    "-i", shifted_sub_path,
                    "-c", "copy",
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=" + subtitle_lang.split(",")[0].strip(),
                    output_path,
                ]
                _run_ffmpeg(task_id, ffmpeg_embed_cmd, start=83, end=95)
            else:
                # Nothing to do, just rename/move
                import shutil
                shutil.move(temp_cut_path, output_path)
        else:
            _append_log(task_id, "[PROCESS] Memproses video (re-encode)...")
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", temp_cut_path]

            if has_sub_file and subtitle_type == "soft":
                ffmpeg_cmd.extend(["-i", shifted_sub_path])

            vf_filters = []
            if video_format == "vertical-crop":
                vf_filters.append("crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'")
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Crop Center)")
            elif video_format == "vertical-pad":
                vf_filters.append("pad='max(iw,ih*9/16)':'max(ih,iw*16/9)':(ow-iw)/2:(oh-ih)/2:black")
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Pad Black Bars)")

            if has_sub_file and subtitle_type == "burn":
                alignment = 5 if subtitle_position == "center" else 2
                margin_v  = 10 if subtitle_position == "center" else 20
                safe_sub = shifted_sub_path.replace("\\", "/").replace(":", "\\:")
                
                # Preset capcut
                primary_c = _rgb_to_ass(sub_primary_color)
                outline_c = _rgb_to_ass(sub_outline_color)
                back_c    = _rgb_to_ass(sub_back_color, sub_back_alpha)
                bold_val  = "1" if sub_bold else "0"
                ital_val  = "1" if sub_italic else "0"
                undr_val  = "1" if sub_underline else "0"
                
                align = "2" # bottom
                if subtitle_position == "center":
                    align = "5"
                    
                force_style = (
                    f"FontSize={sub_fontsize},"
                    f"Alignment={align},"
                    f"Bold={bold_val},"
                    f"Italic={ital_val},"
                    f"Underline={undr_val},"
                    f"PrimaryColour={primary_c},"
                    f"OutlineColour={outline_c},"
                    f"BackColour={back_c},"
                    f"BorderStyle={sub_border_style},"
                    f"Outline={sub_outline_width},"
                    f"Shadow={sub_shadow}"
                )
                vf_filters.append(f"subtitles='{safe_sub}':force_style='{force_style}'")
                _append_log(task_id, f"[CC] Membakar subtitle ke video (posisi: {subtitle_position})...")

            if has_hook_title and os.path.isfile(temp_hook_sub_path):
                safe_hook_sub = temp_hook_sub_path.replace("\\", "/").replace(":", "\\:")
                
                # Map presets to ASS style string properties
                preset_styles = {
                    "yellow-pop": "PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=2",
                    "tiktok": "PrimaryColour=&H005C3BFF,OutlineColour=&H00FFFFFF,BorderStyle=1,Outline=3,Shadow=0",
                    "white-box": "PrimaryColour=&H00FFFFFF,BackColour=&H80000000,BorderStyle=3,Outline=0,Shadow=0",
                    "neon": "PrimaryColour=&H00FFFF00,OutlineColour=&H00333300,BorderStyle=1,Outline=2,Shadow=5",
                    "classic": "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1",
                }
                selected_style = preset_styles.get(hook_preset, preset_styles["yellow-pop"])
                
                hook_style = f"FontSize={hook_fontsize},Alignment=8,Bold=1,{selected_style},MarginV=40"
                vf_filters.append(f"subtitles='{safe_hook_sub}':force_style='{hook_style}'")
                _append_log(task_id, f"[HOOK] Membakar judul hook ke video (Style: {hook_preset}, Size: {hook_fontsize})...")

            if vf_filters:
                ffmpeg_cmd.extend(["-vf", ",".join(vf_filters)])

            ffmpeg_cmd.extend([
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "copy",
            ])

            if has_sub_file and subtitle_type == "soft":
                ffmpeg_cmd.extend([
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=" + subtitle_lang.split(",")[0].strip(),
                ])

            ffmpeg_cmd.append(output_path)
            _run_ffmpeg(task_id, ffmpeg_cmd, start=50, end=85, start_str=start, end_str=end)

        # ── Step 5: Cleanup ──────────────────────────────────────────────────
        for f in [downloaded_file, subtitle_file, shifted_sub_path, temp_hook_sub_path]:
            if f and os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

        _append_log(task_id, f"[DONE] Selesai! File tersimpan: {output_filename}")
        _update_task(task_id, status="done", progress=100, output_file=output_filename)

    except Exception as exc:
        _append_log(task_id, f"[ERR] Error: {exc}")
        _update_task(task_id, status="error", error=str(exc))

        for f in [output_path, temp_cut_path]:
            if f and os.path.isfile(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


def _run_ffmpeg(
    task_id: str,
    cmd: list,
    start: int = 0,
    end: int = 100,
    start_str: str = "0",
    end_str: str = "0",
):
    """Run an FFmpeg command and stream its output to task logs with progress mapping."""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _append_log(task_id, line)

                m = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
                if m:
                    h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    elapsed = h * 3600 + mn * 60 + s
                    try:
                        end_sec   = _parse_seconds(end_str) if end_str != "0" else elapsed
                        start_sec = _parse_seconds(start_str) if start_str != "0" else 0
                        duration  = max(end_sec - start_sec, 1)
                        pct = min(elapsed / duration, 1.0)
                        _update_task(task_id, progress=int(start + pct * (end - start)))
                    except Exception:
                        pass

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg keluar dengan kode error: {proc.returncode}")


def start_clip_thread(
    task_id: str,
    url: str,
    start: str,
    end: str,
    output_dir: str,
    subtitle_enabled: bool = False,
    subtitle_lang: str = "id,en",
    subtitle_type: str = "soft",
    subtitle_auto: bool = True,
    subtitle_position: str = "bottom",
    sub_fontsize: str = "20",
    sub_case: str = "normal",
    sub_bold: bool = False,
    sub_italic: bool = False,
    sub_underline: bool = False,
    video_format: str = "original",
    sub_primary_color: str = "FFFFFF",
    sub_outline_color: str = "000000",
    sub_back_color: str = "000000",
    sub_back_alpha: str = "80",
    sub_border_style: str = "1",
    sub_outline_width: str = "2",
    sub_shadow: str = "1",
    hook_title: str = "",
    hook_fontsize: str = "34",
    hook_preset: str = "yellow-pop",
    cookies: str = "",
):
    """
    Menjalankan proses pemotongan di thread terpisah."""
    t = threading.Thread(
        target=run_clip,
        args=(task_id, url, start, end, output_dir,
              subtitle_enabled, subtitle_lang, subtitle_type,
              subtitle_auto, subtitle_position, sub_fontsize, sub_case,
              sub_bold, sub_italic, sub_underline, video_format,
              sub_primary_color, sub_outline_color, sub_back_color,
              sub_back_alpha, sub_border_style, sub_outline_width, sub_shadow,
              hook_title, hook_fontsize, hook_preset, cookies),
        daemon=True,
    )
    t.start()
    return t
