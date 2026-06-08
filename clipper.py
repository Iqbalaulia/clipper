import subprocess
import os
import uuid
import threading
import re
import sys
from typing import Dict, Any

# Shared progress state across tasks
_tasks: Dict[str, Any] = {}
_lock = threading.Lock()
# Global lock to prevent multiple yt-dlp instances from downloading the same video concurrently
DOWNLOAD_LOCK = threading.Lock()


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


def _find_subtitle_file(output_dir: str, task_id: str, lang: str, video_id: str = "") -> str | None:
    """
    Find a subtitle file for this specific task/video.
    Priority: task-specific download (_sub_dl_{task_id}) > video cache (_cache_{video_id}) > scan (_scan_{video_id}).
    Always filters by video_id to prevent cross-video contamination.
    """
    task_prefix = f"_sub_dl_{task_id}"
    cache_prefix = f"_cache_{video_id}" if video_id else None
    scan_prefix  = f"_scan_{video_id}"  if video_id else None

    buckets = {"task": [], "cache": [], "scan": []}  # priority order

    for f in os.listdir(output_dir):
        fname = f.lower()
        if not (fname.endswith(".srt") or fname.endswith(".vtt")):
            continue
        if f.startswith(task_prefix):
            buckets["task"].append(os.path.join(output_dir, f))
        elif cache_prefix and f.startswith(cache_prefix):
            buckets["cache"].append(os.path.join(output_dir, f))
        elif scan_prefix and f.startswith(scan_prefix):
            buckets["scan"].append(os.path.join(output_dir, f))

    candidates = buckets["task"] or buckets["cache"] or buckets["scan"]
    if not candidates:
        return None

    # Prefer file matching the requested language
    for c in candidates:
        if f".{lang}." in os.path.basename(c):
            return c
    # Fallback: prefer .srt over .vtt
    srt_candidates = [c for c in candidates if c.lower().endswith(".srt")]
    return srt_candidates[0] if srt_candidates else candidates[0]


def _normalize_ts_to_srt(ts: str) -> str:
    """
    Normalize a timestamp from VTT (HH:MM:SS.mmm or MM:SS.mmm) or
    SRT (HH:MM:SS,mmm) format into SRT format (HH:MM:SS,mmm).
    """
    ts = ts.strip()
    # Replace dot separator with comma (VTT -> SRT)
    ts = ts.replace(".", ",")
    parts = ts.split(":")
    if len(parts) == 2:
        # MM:SS,mmm -> 00:MM:SS,mmm
        ts = "00:" + ts
    return ts


def _strip_sub_tags(text: str) -> str:
    """Remove HTML/VTT tags and cue settings from subtitle text lines."""
    # Remove <c>, <b>, <i>, <u>, </c>, etc. and timestamp tags <00:01:02.345>
    text = re.sub(r"<[^>]+>", "", text)
    # Remove VTT positioning cues that appear after timestamp lines (align:start ...)
    text = re.sub(r"\s*(align|position|line|size):[^\s]+", "", text)
    return text.strip()


def _shift_srt(
    input_path: str,
    offset_sec: float,
    output_path: str,
    text_case: str = "normal",
):
    """
    Parse an SRT or VTT file, shift all timestamps back by `offset_sec`,
    drop entries outside the clip window, strip HTML tags, and write
    a clean SRT file.  Returns the number of subtitle entries written.
    """

    def _ts_to_ms(ts: str) -> int:
        """HH:MM:SS,mmm or HH:MM:SS.mmm -> milliseconds"""
        ts = ts.strip().replace(".", ",")
        parts = ts.split(":")
        if len(parts) == 2:          # MM:SS,mmm
            parts = ["00"] + parts
        h, m, rest = parts
        sec_parts = rest.split(",")
        s  = sec_parts[0]
        ms = sec_parts[1] if len(sec_parts) > 1 else "0"
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms[:3].ljust(3, "0"))

    def _ms_to_srt_ts(ms: int) -> str:
        ms = max(ms, 0)
        h  = ms // 3600000;  ms %= 3600000
        m  = ms // 60000;    ms %= 60000
        s  = ms // 1000;     ms %= 1000
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    offset_ms = int(offset_sec * 1000)

    with open(input_path, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    # Remove WEBVTT header line and NOTE blocks
    content = re.sub(r"^WEBVTT.*\n?", "", content)
    content = re.sub(r"NOTE\b.*?(?=\n\n|\Z)", "", content, flags=re.DOTALL)

    blocks = re.split(r"\n{2,}", content.strip())
    result_blocks = []
    new_index = 1

    for block in blocks:
        lines = [ln.rstrip() for ln in block.strip().splitlines()]
        if not lines:
            continue

        # Find timestamp line
        tc_line_idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                tc_line_idx = i
                break
        if tc_line_idx is None:
            continue

        tc_raw = lines[tc_line_idx]
        # The timestamp line may have VTT cue settings after the end time
        # e.g. "00:01:30.000 --> 00:01:35.500 align:start position:0%"
        m = re.match(r"([\d:.]+(?:,[\d]+)?)\s*-->\s*([\d:.]+(?:,[\d]+)?)(.*)", tc_raw)
        if not m:
            continue

        try:
            start_ms = _ts_to_ms(m.group(1)) - offset_ms
            end_ms   = _ts_to_ms(m.group(2)) - offset_ms
        except Exception:
            continue  # malformed timestamp — skip block

        if end_ms < 0:
            continue

        new_tc = f"{_ms_to_srt_ts(max(start_ms, 0))} --> {_ms_to_srt_ts(end_ms)}"
        text_lines = lines[tc_line_idx + 1:]

        # Clean up text lines
        cleaned = []
        for tl in text_lines:
            tl = _strip_sub_tags(tl)
            if tl:  # skip blank lines inside block
                cleaned.append(tl)
        if not cleaned:
            continue  # no visible text — skip block

        if text_case == "upper":
            cleaned = [tl.upper() for tl in cleaned]

        result_blocks.append(f"{new_index}\n{new_tc}\n" + "\n".join(cleaned))
        new_index += 1

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(result_blocks) + ("\n" if result_blocks else ""))

    return len(result_blocks)


def _validate_srt_file(path: str) -> bool:
    """Return True if the SRT file exists and has at least one subtitle entry."""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        return bool(re.search(r"\d+:\d+:\d+,\d+\s*-->\s*\d+:\d+:\d+,\d+", content))
    except Exception:
        return False


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
    hook_position: str = "top",
    cookies: str = "",
):
    """
    Full pipeline: download → (download subtitles) → cut → (embed subtitles) → cleanup.
    Runs in a background thread.
    """
    os.makedirs(output_dir, exist_ok=True)

    output_filename  = f"clip_{task_id}.mp4"
    output_path      = os.path.join(output_dir, output_filename)
    temp_cut_path    = os.path.join(output_dir, f"_tmpcut_{task_id}.mp4")
    temp_hook_sub_path = os.path.join(output_dir, f"_hook_{task_id}.srt")

    try:
        # ── Step 1: Download video (+ optionally subtitles) ─────────────────
        _update_task(task_id, status="downloading", progress=5)
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
        video_id = video_id_match.group(1) if video_id_match else task_id
        cache_video_path = os.path.join(output_dir, f"_cache_{video_id}.mp4")

        downloaded_file = None
        has_sub_file = False
        sub_file_path = ""

        with DOWNLOAD_LOCK:
            if os.path.isfile(cache_video_path):
                _append_log(task_id, "[>>] Video sudah ada di cache. Menggunakan file lokal...")
                downloaded_file = cache_video_path

                # Even on video cache hit, download a FRESH task-specific subtitle
                # so each concurrent task has its own copy and they don't conflict.
                if subtitle_enabled:
                    _append_log(task_id, "[>>] Mengunduh subtitle (task-specific)...")
                    first_lang = subtitle_lang.split(",")[0].strip()
                    sub_dl_path = os.path.join(output_dir, f"_sub_dl_{task_id}")
                    sub_cmd = [
                        sys.executable, "-m", "yt_dlp",
                        "--write-auto-sub", "--write-sub",
                        "--sub-lang", subtitle_lang,
                        "--convert-subs", "srt",
                        "--skip-download",
                        "--js-runtimes", "node:node.exe",
                        "--remote-components", "ejs:github",
                        "--no-check-certificates",
                        "--output", sub_dl_path,
                        "--no-playlist",
                    ]
                    if cookies:
                        sub_cmd += ["--cookies", cookies]
                    sub_cmd.append(url)
                    subprocess.run(
                        sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace"
                    )
                    # Check for the task-specific download
                    for fname in os.listdir(output_dir):
                        if fname.startswith(f"_sub_dl_{task_id}") and \
                                (fname.lower().endswith(".srt") or fname.lower().endswith(".vtt")):
                            has_sub_file = True
                            sub_file_path = os.path.join(output_dir, fname)
                            break
                    # Fallback: use scan file from detect-moments for this video_id
                    if not has_sub_file:
                        for ext in ("srt", "vtt"):
                            fallback = os.path.join(output_dir, f"_scan_{video_id}.{first_lang}.{ext}")
                            if os.path.isfile(fallback):
                                has_sub_file = True
                                sub_file_path = fallback
                                _append_log(task_id, "[>>] Menggunakan subtitle dari hasil Scan sebelumnya.")
                                break
                    if not has_sub_file:
                        _append_log(task_id, "⚠️ Gagal mengunduh subtitle. Klip akan dilanjutkan tanpa subtitle.")
            else:
                _append_log(task_id, "[>>] Memulai unduhan video...")

                yt_dlp_cmd = [
                    sys.executable, "-m", "yt_dlp",
                    "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "--merge-output-format", "mp4",
                    "--output", cache_video_path,
                    "--js-runtimes", "node:node.exe",
                    "--remote-components", "ejs:github",
                    "--no-check-certificates",
                    "--no-playlist",
                    "--progress",
                    "--newline",
                ]

                if cookies:
                    yt_dlp_cmd += ["--cookies", cookies]

                yt_dlp_cmd.append(url)
                _append_log(task_id, f"[>>] Perintah yt-dlp video: {' '.join(yt_dlp_cmd)}")

                proc = subprocess.Popen(
                    yt_dlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace"
                )

                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        line = line.strip()
                        # Filter output for progress
                        if "[download]" in line and "%" in line:
                            m = re.search(r"\[download\]\s+([\d.]+)%", line)
                            if m:
                                _update_task(task_id, progress=int(5 + float(m.group(1)) * 0.55))
                        elif "[youtube]" in line or "ERROR:" in line or "[Merger]" in line:
                            _append_log(task_id, line)

                proc.wait()

                if proc.returncode != 0:
                    _append_log(task_id, "⚠️ Peringatan: yt-dlp melaporkan error (mungkin sebagian unduhan gagal), memverifikasi file...")

                if os.path.isfile(cache_video_path):
                    downloaded_file = cache_video_path
                else:
                    candidates = [
                        os.path.join(output_dir, f)
                        for f in os.listdir(output_dir)
                        if f.startswith(f"_cache_{video_id}")
                        and not f.endswith(".part")
                        and not f.endswith(".srt")
                        and not f.endswith(".vtt")
                    ]
                    if candidates:
                        downloaded_file = candidates[0]

                if not downloaded_file:
                    raise RuntimeError(f"Gagal mengunduh video (Exit Code: {proc.returncode}). YouTube mungkin memblokir akses atau URL tidak valid.")

                # Download subtitle to task-specific path (not shared cache)
                if subtitle_enabled:
                    _append_log(task_id, "[>>] Mengunduh subtitle (task-specific)...")
                    first_lang = subtitle_lang.split(",")[0].strip()
                    sub_dl_path = os.path.join(output_dir, f"_sub_dl_{task_id}")
                    sub_cmd = [
                        sys.executable, "-m", "yt_dlp",
                        "--write-auto-sub", "--write-sub",
                        "--sub-lang", subtitle_lang,
                        "--convert-subs", "srt",
                        "--skip-download",
                        "--js-runtimes", "node:node.exe",
                        "--remote-components", "ejs:github",
                        "--no-check-certificates",
                        "--output", sub_dl_path,
                        "--no-playlist",
                    ]
                    if cookies:
                        sub_cmd += ["--cookies", cookies]
                    sub_cmd.append(url)
                    subprocess.run(
                        sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace"
                    )
                    # Find the task-specific subtitle file
                    for fname in os.listdir(output_dir):
                        if fname.startswith(f"_sub_dl_{task_id}") and \
                                (fname.lower().endswith(".srt") or fname.lower().endswith(".vtt")):
                            has_sub_file = True
                            sub_file_path = os.path.join(output_dir, fname)
                            break
                    # Fallback: scan file from detect-moments
                    if not has_sub_file:
                        for ext in ("srt", "vtt"):
                            fallback = os.path.join(output_dir, f"_scan_{video_id}.{first_lang}.{ext}")
                            if os.path.isfile(fallback):
                                has_sub_file = True
                                sub_file_path = fallback
                                _append_log(task_id, "[>>] Menggunakan subtitle dari hasil Scan sebelumnya.")
                                break
                    if not has_sub_file:
                        _append_log(task_id, "⚠️ Gagal mengunduh subtitle (Mungkin limit HTTP 429). Klip akan dilanjutkan tanpa subtitle.")

        _append_log(task_id, f"[>>] File video siap: {downloaded_file}")

        # ── Step 2: Find & process subtitle file ────────────────────────────
        subtitle_file    = None
        shifted_sub_path = None

        if subtitle_enabled:
            _update_task(task_id, status="subtitles", progress=62)
            _append_log(task_id, "[CC] Mencari file subtitle yang diunduh...")

            first_lang = subtitle_lang.split(",")[0].strip()

            # Use sub_file_path found in Step 1 (task-specific download)
            if has_sub_file and sub_file_path and os.path.isfile(sub_file_path):
                subtitle_file = sub_file_path
                _append_log(task_id, f"[CC] Menggunakan subtitle: {os.path.basename(subtitle_file)}")
            else:
                # Broad fallback filtered by video_id to prevent wrong-video contamination
                subtitle_file = _find_subtitle_file(output_dir, task_id, first_lang, video_id)

            if subtitle_file:
                _append_log(task_id, f"[CC] Ditemukan: {os.path.basename(subtitle_file)}")
                # Shift subtitle timestamps to match clip start
                shifted_sub_path = os.path.join(output_dir, f"_sub_{task_id}.srt")
                start_sec = _parse_seconds(start)
                
                # If burning subtitles, we use one-pass accurate seek which starts decoder at fast_seek_sec.
                # So the subtitle must be shifted by fast_seek_sec, not start_sec.
                # For soft subs, the output video starts exactly at start_sec, so we shift by start_sec.
                fast_seek_sec = max(0.0, start_sec - 30.0)
                sub_shift_sec = fast_seek_sec if subtitle_type == "burn" else start_sec
                
                entry_count = _shift_srt(subtitle_file, sub_shift_sec, shifted_sub_path, sub_case)
                if entry_count == 0 or not _validate_srt_file(shifted_sub_path):
                    _append_log(task_id, "[!] Subtitle kosong setelah diproses (clip mungkin di luar jangkauan subtitle) — lanjut tanpa subtitle.")
                    subtitle_enabled = False
                    shifted_sub_path = None
                else:
                    _append_log(task_id, f"[CC] Timestamp disesuaikan. {entry_count} baris subtitle siap.")
            else:
                _append_log(task_id, "[!] Subtitle tidak ditemukan — lanjut tanpa subtitle.")
                subtitle_enabled = False

        _update_task(task_id, status="processing", progress=76)

        start_sec = _parse_seconds(start)
        fast_seek_sec = max(0.0, start_sec - 30.0)
        acc_seek_sec  = start_sec - fast_seek_sec

        has_hook_title = bool(hook_title)
        needs_reencode  = video_format != "original" or (subtitle_enabled and subtitle_type == "burn") or has_hook_title
        has_sub_file    = subtitle_enabled and shifted_sub_path and os.path.isfile(shifted_sub_path)
        burn_subtitle   = has_sub_file and subtitle_type == "burn"

        if has_hook_title:
            _append_log(task_id, f"[HOOK] Membuat judul hook: {hook_title}")
            # If using one-pass accurate seek, the filtergraph PTS is offset by fast_seek_sec.
            # The output -ss acc_seek_sec drops frames up to acc_seek_sec.
            # So the hook title must be burned starting at acc_seek_sec to appear at the start of the output video.
            hook_start = acc_seek_sec if needs_reencode else 0.0
            hook_end = hook_start + 6.0   # Tampil 6 detik — cukup untuk menarik perhatian

            
            def format_srt_time(seconds):
                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                s = int(seconds % 60)
                ms = int((seconds % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
                
            start_str = format_srt_time(hook_start)
            end_str = format_srt_time(hook_end)
            with open(temp_hook_sub_path, "w", encoding="utf-8") as f:
                f.write(f"1\n{start_str} --> {end_str}\n{hook_title}\n")

        # ── Step 3: Cut video ────────────────────────────────────────────────
        # For ANY re-encode (burn-in, format change, hook title) → ONE PASS directly from the source video
        # using two-stage accurate seek so timing is frame-perfect.
        # For stream-copy only → fast cut to temp file first.
        skip_temp_cut = needs_reencode

        if not skip_temp_cut:
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

            if skip_temp_cut:
                # ── ONE-PASS from original with accurate two-stage seek ──────
                # Fast seek to 30s before target (fast), then accurate seek
                # remaining (decode up to 30s). Output starts EXACTLY at
                # requested start_sec so timing is frame-perfect.
                _update_task(task_id, status="cutting", progress=65)
                _append_log(task_id, f"[CUT] Pemotongan presisi dari {start} hingga {end}...")
                
                try:
                    duration = _parse_seconds(end) - _parse_seconds(start)
                    duration_ff = str(max(duration, 1.0))
                except Exception:
                    duration_ff = _seconds_to_ffmpeg(end) # Fallback

                ffmpeg_cmd = ["ffmpeg", "-y"]
                if fast_seek_sec > 0:
                    ffmpeg_cmd.extend(["-ss", f"{fast_seek_sec:.3f}"])
                ffmpeg_cmd.extend(["-i", downloaded_file])
                # Explicit stream mapping: always pick first video + first audio
                ffmpeg_cmd.extend(["-map", "0:v:0", "-map", "0:a:0"])
                if acc_seek_sec > 0:
                    ffmpeg_cmd.extend(["-ss", f"{acc_seek_sec:.3f}"])
                ffmpeg_cmd.extend(["-t", duration_ff])
            else:
                ffmpeg_cmd = ["ffmpeg", "-y", "-i", temp_cut_path]
                ffmpeg_cmd.extend(["-map", "0:v:0", "-map", "0:a:0"])

            if has_sub_file and subtitle_type == "soft":
                ffmpeg_cmd.extend(["-i", shifted_sub_path])
                ffmpeg_cmd.extend(["-map", "1:s:0"])  # Map subtitle from second input

            vf_filters = []
            if video_format == "vertical-crop":
                vf_filters.append("crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'")
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Crop Center)")
            elif video_format == "vertical-pad":
                vf_filters.append("pad='max(iw,ih*9/16)':'max(ih,iw*16/9)':(ow-iw)/2:(oh-ih)/2:black")
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Pad Black Bars)")

            if has_sub_file and subtitle_type == "burn":
                # Build safe path for FFmpeg subtitles filter on Windows
                # Use pathlib to convert backslashes -> forward slashes correctly,
                # then escape the drive-letter colon (e.g. d:/ -> d\:/)
                import pathlib
                safe_sub = pathlib.Path(shifted_sub_path).as_posix()
                safe_sub = re.sub(r"([A-Za-z]):/", r"\1\\:/", safe_sub)
                # Escape single quotes inside the path (rare but possible)
                safe_sub = safe_sub.replace("'", "\\'")

                primary_c = _rgb_to_ass(sub_primary_color)
                outline_c = _rgb_to_ass(sub_outline_color)
                back_c    = _rgb_to_ass(sub_back_color, sub_back_alpha)
                bold_val  = "1" if sub_bold else "0"
                ital_val  = "1" if sub_italic else "0"
                undr_val  = "1" if sub_underline else "0"

                align = "2"  # bottom
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
                import pathlib
                safe_hook_sub = pathlib.Path(temp_hook_sub_path).as_posix()
                safe_hook_sub = re.sub(r"([A-Za-z]):/", r"\1\\:/", safe_hook_sub)
                safe_hook_sub = safe_hook_sub.replace("'", "\\'")

                hook_style_presets = {
                    "yellow-pop": {
                        "font":         "Impact",
                        "primary":      "&H0000FFFF",
                        "outline":      "&H00000000",
                        "back":         "&H00000000",
                        "border_style": "1",
                        "outline_w":    "4",
                        "shadow":       "3",
                    },
                    "tiktok": {
                        "font":         "Impact",
                        "primary":      "&H005C3BFF",
                        "outline":      "&H00FFFFFF",
                        "back":         "&H00000000",
                        "border_style": "1",
                        "outline_w":    "4",
                        "shadow":       "0",
                    },
                    "white-box": {
                        "font":         "Arial",
                        "primary":      "&H00FFFFFF",
                        "outline":      "&H00000000",
                        "back":         "&HAA000000",
                        "border_style": "3",
                        "outline_w":    "0",
                        "shadow":       "0",
                    },
                    "neon": {
                        "font":         "Impact",
                        "primary":      "&H00FFFF00",
                        "outline":      "&H00333300",
                        "back":         "&H00000000",
                        "border_style": "1",
                        "outline_w":    "3",
                        "shadow":       "8",
                    },
                    "classic": {
                        "font":         "Arial",
                        "primary":      "&H00FFFFFF",
                        "outline":      "&H00000000",
                        "back":         "&H00000000",
                        "border_style": "1",
                        "outline_w":    "3",
                        "shadow":       "2",
                    },
                    "fire": {
                        "font":         "Impact",
                        "primary":      "&H000045FF",
                        "outline":      "&H00000000",
                        "back":         "&H00000000",
                        "border_style": "1",
                        "outline_w":    "5",
                        "shadow":       "4",
                    },
                    "breaking": {
                        "font":         "Impact",
                        "primary":      "&H00FFFFFF",
                        "outline":      "&H000000CC",
                        "back":         "&H880000CC",
                        "border_style": "3",
                        "outline_w":    "0",
                        "shadow":       "0",
                    },
                }

                ps = hook_style_presets.get(hook_preset, hook_style_presets["yellow-pop"])
                
                align_hook = "8" # top
                if hook_position == "center":
                    align_hook = "5"
                elif hook_position == "bottom":
                    align_hook = "2"
                
                hook_style = (
                    f"FontName={ps['font']},"
                    f"FontSize={hook_fontsize},"
                    f"Alignment={align_hook},"
                    f"Bold=1,"
                    f"PrimaryColour={ps['primary']},"
                    f"OutlineColour={ps['outline']},"
                    f"BackColour={ps['back']},"
                    f"BorderStyle={ps['border_style']},"
                    f"Outline={ps['outline_w']},"
                    f"Shadow={ps['shadow']},"
                    f"MarginV=50"
                )
                vf_filters.append(f"subtitles='{safe_hook_sub}':force_style='{hook_style}'")
                _append_log(task_id, f"[HOOK] Membakar judul hook ke video (Preset: {hook_preset}, Font: {ps['font']}, Size: {hook_fontsize})...")

            if vf_filters:
                ffmpeg_cmd.extend(["-vf", ",".join(vf_filters)])

            ffmpeg_cmd.extend([
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",          # Force 8-bit 4:2:0 — browser-compatible
                "-profile:v", "high", "-level", "4.0",  # Max browser compat H.264 profile
                "-c:a", "aac", "-b:a", "192k",  # Re-encode audio to AAC (avoids Opus/Vorbis in MP4)
                "-movflags", "+faststart",      # Web-friendly: moov atom at start of file
                "-shortest",                    # End at shortest stream to avoid empty frames
            ])

            if has_sub_file and subtitle_type == "soft":
                ffmpeg_cmd.extend([
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=" + subtitle_lang.split(",")[0].strip(),
                ])

            ffmpeg_cmd.append(output_path)
            _run_ffmpeg(task_id, ffmpeg_cmd, start=50, end=85, start_str=start, end_str=end)

        # ── Step 5: Cleanup ──────────────────────────────────────────────────
        # Delete ONLY task-specific temp files. The video cache (_cache_{video_id}.mp4)
        # is intentionally kept for reuse on subsequent clips of the same video.
        temp_files_to_delete = [
            temp_cut_path,          # _tmpcut_{task_id}.mp4
            shifted_sub_path,       # _sub_{task_id}.srt  (shifted subtitle)
            temp_hook_sub_path,     # _hook_{task_id}.srt
        ]
        # Also delete the task-specific subtitle download (sub_file_path if it's task-specific)
        if sub_file_path and os.path.basename(sub_file_path).startswith(f"_sub_dl_{task_id}"):
            temp_files_to_delete.append(sub_file_path)

        for f in temp_files_to_delete:
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
    hook_position: str = "top",
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
              hook_title, hook_fontsize, hook_preset, hook_position, cookies),
        daemon=True,
    )
    t.start()
    return t
