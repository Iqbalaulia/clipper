import subprocess
import os
import uuid

# Pastikan runtime Node.js bawaan (node.exe di folder proyek) bisa ditemukan
# oleh subprocess yt-dlp meskipun aplikasi dijalankan dari cwd lain.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ["PATH"] = _PROJECT_DIR + os.pathsep + os.environ.get("PATH", "")
import threading
import re
import sys
from typing import Dict, Any
import face_tracker
import models
import runner
import virality
import thumbnail
import cloud_storage

# Global lock to prevent multiple yt-dlp instances from downloading the same video concurrently
DOWNLOAD_LOCK = threading.Lock()


def create_task() -> str:
    """Create a new task and return its ID. (kept for compatibility)"""
    task_id = str(uuid.uuid4())
    models.create_task(task_id)
    return task_id


def get_task(task_id: str, user_id: int | None = None) -> dict | None:
    return models.get_task(task_id, user_id=user_id)


def get_tasks_batch(task_ids: list, user_id: int | None = None) -> dict:
    """Return a dict of {task_id: task_data} for all given task IDs."""
    return {tid: models.get_task(tid, user_id=user_id) for tid in task_ids}


def _update_task(task_id: str, **kwargs):
    models.update_task(task_id, **kwargs)


def _append_log(task_id: str, message: str):
    models.append_log(task_id, message)


def _seconds_to_ffmpeg(ts: str) -> str:
    """
    Accept HH:MM:SS, MM:SS, or raw seconds string.
    Returns the string as-is if already valid for FFmpeg.
    """
    ts = ts.strip()
    if re.fullmatch(r"[\d.]+", ts):
        return ts
    return ts


def _build_ytdlp_formats(download_resolution: str) -> tuple[str, str]:
    """
    Build yt-dlp format strings for a requested max resolution.
    Returns (primary_format, fallback_format).
    """
    valid = {"2160", "1440", "1080", "720", "480"}
    if download_resolution == "best" or download_resolution not in valid:
        return (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "best[ext=mp4]/best",
        )
    h = download_resolution
    return (
        f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/best[height<={h}][ext=mp4]/best[height<={h}]",
        f"best[height<={h}][ext=mp4]/best[height<={h}]",
    )


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


# ── Sentence-aware helpers ─────────────────────────────────────────────────

# Punctuation marks that typically end a sentence in Indonesian / English.
# Includes: . ? ! … । and the Arabic question mark ؟
_SENTENCE_END_RE = re.compile(r'[.?!…।؟]+\s*$')


def _segment_sentences(segments: list) -> list:
    """
    Merge consecutive subtitle entries into full sentences.

    A subtitle block may split a single sentence across multiple entries.
    This function groups entries until it finds an end-of-sentence punctuation
    mark, then starts a new sentence.

    Returns a list of dicts: {start, end, text} where each item is one sentence.
    """
    if not segments:
        return []

    sentences = []
    current = None

    for seg in segments:
        seg_start = seg.get("start", "")
        seg_end = seg.get("end", "")
        seg_text = (seg.get("text") or "").strip()
        if not seg_text:
            continue

        if current is None:
            current = {"start": seg_start, "end": seg_end, "text": seg_text}
        else:
            current["end"] = seg_end
            # Insert a space when concatenating, but avoid double spaces.
            if current["text"].endswith("-"):
                current["text"] = current["text"][:-1] + seg_text
            else:
                current["text"] = (current["text"] + " " + seg_text).strip()

        # If the accumulated text ends with sentence-final punctuation,
        # close the current sentence.
        if _SENTENCE_END_RE.search(current["text"]):
            sentences.append(current)
            current = None

    # Append any trailing fragment as its own sentence.
    if current is not None:
        sentences.append(current)

    return sentences


def _seconds_from_ts(ts: str) -> float:
    """Convert HH:MM:SS or MM:SS timestamp to seconds."""
    ts = ts.strip()
    parts = ts.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 1:
        return parts[0]
    return 0.0


def _seconds_to_hhmmss(seconds: float) -> str:
    """Convert seconds to HH:MM:SS string."""
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _parse_srt(srt_text: str) -> list:
    """
    Parse raw SRT content into list of {start, end, text} dicts.
    Lightweight local parser to avoid circular imports with app.py.
    """
    clean = re.sub(r'<[^>]+>', '', srt_text)
    blocks = re.split(r'\n\s*\n', clean.strip())
    segments = []
    time_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2})[,.]\d{3}\s*-->\s*(\d{2}:\d{2}:\d{2})[,.]\d{3}'
    )
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        ts_line = None
        text_lines = []
        for line in lines:
            if time_pattern.match(line):
                ts_line = line
            elif ts_line and not line.isdigit():
                text_lines.append(line)
        if not ts_line or not text_lines:
            continue
        m = time_pattern.match(ts_line)
        if not m:
            continue
        text = ' '.join(text_lines).strip()
        if not text:
            continue
        segments.append({"start": m.group(1), "end": m.group(2), "text": text})
    return segments


def _extract_clip_segments(srt_path: str, start_sec: float, end_sec: float) -> list:
    """
    Parse an SRT/VTT file and return segments that overlap with [start_sec, end_sec].
    Returned segments use relative timestamps so they are suitable for virality scoring.
    """
    if not srt_path or not os.path.isfile(srt_path):
        return []
    try:
        with open(srt_path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except Exception:
        return []

    all_segments = _parse_srt(raw)
    clip_segments = []
    for seg in all_segments:
        s = _seconds_from_ts(seg["start"])
        e = _seconds_from_ts(seg["end"])
        # Overlap test
        if e >= start_sec and s <= end_sec:
            rel_start = max(0.0, s - start_sec)
            rel_end = max(0.0, e - start_sec)
            clip_segments.append({
                "start": _seconds_to_hhmmss(rel_start),
                "end": _seconds_to_hhmmss(rel_end),
                "text": seg["text"],
            })
    return clip_segments


def _snap_to_sentence_boundaries(
    segments: list,
    start_sec: float,
    end_sec: float,
    duration_secs: float | None = None,
    min_duration: float = 15.0,
    max_duration: float = 180.0,
) -> tuple[float, float]:
    """
    Snap start/end timestamps to the nearest sentence boundaries.

    Rules:
      - If start falls inside a sentence, move it to the beginning of that
        sentence so the clip starts with full context.
      - If end falls inside a sentence, extend it to the end of that sentence
        so the clip finishes the thought.
      - Clamp result to [0, duration_secs] if provided.
      - Keep result within [min_duration, max_duration]. If snapping pushes
        the clip outside these bounds, fall back to the original timestamps
        clamped to valid limits.

    Returns (snapped_start, snapped_end).
    """
    if not segments:
        return start_sec, end_sec

    sentences = _segment_sentences(segments)
    if not sentences:
        return start_sec, end_sec

    original_start, original_end = start_sec, end_sec

    # Snap start: find the sentence that contains start_sec and move to its start.
    snapped_start = start_sec
    for sent in sentences:
        s = _seconds_from_ts(sent["start"])
        e = _seconds_from_ts(sent["end"])
        if s <= start_sec <= e:
            snapped_start = s
            break
    else:
        # start_sec is after the last sentence -> keep as-is.
        snapped_start = start_sec

    # Snap end: find the sentence that contains end_sec and move to its end.
    snapped_end = end_sec
    for sent in sentences:
        s = _seconds_from_ts(sent["start"])
        e = _seconds_from_ts(sent["end"])
        if s <= end_sec <= e:
            snapped_end = e
            break
    else:
        snapped_end = end_sec

    # Clamp to video duration.
    if duration_secs is not None:
        snapped_start = max(0.0, min(snapped_start, duration_secs))
        snapped_end = max(0.0, min(snapped_end, duration_secs))

    # Ensure end > start.
    if snapped_end <= snapped_start:
        snapped_end = min(snapped_start + 60.0, duration_secs if duration_secs else snapped_start + 60.0)

    duration = snapped_end - snapped_start

    # If snapping produced an unreasonably short or long clip, fall back to
    # the original timestamps (clamped). This protects against malformed data
    # while still allowing sentence-aware expansion when it makes sense.
    if duration < min_duration or duration > max_duration:
        snapped_start = max(0.0, original_start)
        snapped_end = original_end
        if duration_secs is not None:
            snapped_end = min(snapped_end, duration_secs)
        if snapped_end <= snapped_start:
            snapped_end = min(snapped_start + 60.0, duration_secs if duration_secs else snapped_start + 60.0)

    return snapped_start, snapped_end


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


def _transcribe_with_whisper(
    task_id: str,
    video_path: str,
    output_dir: str,
    model_size: str = "base",
    language: str = "",
) -> str | None:
    """
    Try to transcribe a video using faster-whisper and write an SRT file.
    Returns the path to the SRT file, or None if unavailable/failed.
    """
    try:
        import whisper_engine
    except ImportError:
        _append_log(task_id, "⚠️ Modul whisper_engine tidak ditemukan.")
        return None

    if not whisper_engine.is_available():
        _append_log(task_id, "⚠️ faster-whisper tidak terinstall. Jalankan: pip install faster-whisper")
        return None

    first_lang = (language or "auto").split(",")[0].strip() or None
    if first_lang == "auto":
        first_lang = None

    _append_log(task_id, f"[CC] Mencoba transkripsi lokal dengan Whisper (model={model_size})...")
    try:
        segments = whisper_engine.transcribe(
            video_path,
            model_size=model_size,
            language=first_lang,
        )
        srt_text = whisper_engine.segments_to_srt(segments)
        srt_path = os.path.join(output_dir, f"_whisper_{task_id}.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_text)
        _append_log(task_id, f"[CC] Whisper selesai: {len(segments)} segmen.")
        return srt_path
    except Exception as e:
        _append_log(task_id, f"⚠️ Whisper gagal: {e}")
        return None


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
    subtitle_style: str = "standard",
):
    """
    Parse an SRT or VTT file, shift all timestamps back by `offset_sec`,
    drop entries outside the clip window, strip HTML tags, and write
    a clean SRT file.  Returns the number of subtitle entries written.
    """

    EMOJI_MAP = {
        "uang": "💸", "kaya": "💰", "duit": "💵", "cuan": "🤑",
        "gila": "🤯", "bom": "💥", "meledak": "🔥",
        "waktu": "⏳", "jam": "⏰", "cepat": "⚡",
        "marah": "😡", "sedih": "😢", "nangis": "😭",
        "senang": "😁", "cinta": "❤️", "sayang": "🥰",
        "api": "🔥", "hot": "🌶️", "panas": "🥵",
        "100": "💯", "oke": "👌", "bagus": "👍",
        "dunia": "🌍", "rumah": "🏠", "mobil": "🚗",
        "belajar": "📚", "buku": "📖", "pintar": "🧠",
        "makan": "🍔", "minum": "🥤", "kopi": "☕",
        "menang": "🏆", "juara": "🏅", "sukses": "🚀"
    }

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
        m = re.match(r"([\d:.]+(?:,[\d]+)?)\s*-->\s*([\d:.]+(?:,[\d]+)?)(.*)", tc_raw)
        if not m:
            continue

        try:
            start_ms = _ts_to_ms(m.group(1)) - offset_ms
            end_ms   = _ts_to_ms(m.group(2)) - offset_ms
        except Exception:
            continue

        if end_ms < 0:
            continue

        text_lines = lines[tc_line_idx + 1:]
        cleaned = []
        for tl in text_lines:
            tl = _strip_sub_tags(tl)
            if tl:
                cleaned.append(tl)
        if not cleaned:
            continue
            
        full_text = " ".join(cleaned)
        
        # Apply case styling (Hormozi forces upper case)
        if text_case == "upper" or subtitle_style == "hormozi":
            full_text = full_text.upper()

        if subtitle_style == "hormozi":
            # Word-by-word chunking (2-3 words per screen)
            words = full_text.split()
            if not words:
                continue
                
            chunk_size = 2  # words per screen
            chunks = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
            
            # Distribute time evenly among chunks
            duration_ms = end_ms - max(start_ms, 0)
            chunk_duration = duration_ms // len(chunks)
            
            current_start = max(start_ms, 0)
            
            for chunk in chunks:
                chunk_end = current_start + chunk_duration
                
                # Check for emojis
                chunk_lower = chunk.lower()
                matched_emoji = ""
                for keyword, emoji in EMOJI_MAP.items():
                    if re.search(r'\b' + re.escape(keyword) + r'\b', chunk_lower):
                        matched_emoji = emoji
                        break
                
                final_chunk_text = chunk
                if matched_emoji:
                    final_chunk_text += f" {matched_emoji}"
                
                new_tc = f"{_ms_to_srt_ts(current_start)} --> {_ms_to_srt_ts(chunk_end)}"
                result_blocks.append(f"{new_index}\n{new_tc}\n{final_chunk_text}")
                
                new_index += 1
                current_start = chunk_end
        else:
            # Standard subtitle rendering
            new_tc = f"{_ms_to_srt_ts(max(start_ms, 0))} --> {_ms_to_srt_ts(end_ms)}"
            result_blocks.append(f"{new_index}\n{new_tc}\n{full_text}")
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


def _detect_broll_timestamps(srt_path: str):
    if not os.path.isfile(srt_path):
        return []
    
    broll_map = {
        "money.mp4": ["uang", "duit", "cuan", "kaya", "miliar"],
        "time.mp4": ["waktu", "jam", "hari", "tahun", "lama"],
        "fire.mp4": ["panas", "api", "marah", "gila", "hancur", "terbakar"]
    }
    
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    
    blocks = re.split(r'\n\s*\n', content.strip())
    time_pattern = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}')
    
    broll_events = []
    
    for block in blocks:
        if len(broll_events) > 0: break # Only 1 b-roll for now to keep ffmpeg simple
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 3: continue
        
        m = time_pattern.search(lines[1])
        if not m: continue
        
        text = " ".join(lines[2:]).lower()
        for broll_file, keywords in broll_map.items():
            if any(kw in text for kw in keywords):
                if os.path.isfile(os.path.join("broll", broll_file)):
                    h, m_min, s, ms = map(int, m.groups())
                    start_sec = h * 3600 + m_min * 60 + s + ms / 1000.0
                    end_sec = start_sec + 2.5
                    broll_events.append({"start": start_sec, "end": end_sec, "file": os.path.join("broll", broll_file)})
                    break
                    
    return broll_events

def _rgb_to_ass(rgb_hex: str, alpha_hex: str = "00") -> str:
    """Convert RGB hex (RRGGBB) to ASS color format (&HAABBGGRR&)."""
    rgb_hex = rgb_hex.lstrip("#").ljust(6, "0")
    r, g, b = rgb_hex[0:2], rgb_hex[2:4], rgb_hex[4:6]
    return f"&H{alpha_hex}{b}{g}{r}&"


def _get_quality_profile(output_quality: str) -> dict:
    """Return FFmpeg quality profile mapping."""
    profiles = {
        "high": {"crf": "18", "preset": "medium", "audio_bitrate": "256k"},
        "standard": {"crf": "22", "preset": "fast", "audio_bitrate": "192k"},
        "draft": {"crf": "28", "preset": "veryfast", "audio_bitrate": "128k"},
    }
    return profiles.get(output_quality, profiles["standard"])


def _vertical_target_height(output_resolution: str, vid_h: int) -> int:
    """Return target height for 9:16 output, never upscaling beyond source."""
    targets = {"1080": 1920, "720": 1280, "480": 854}
    if output_resolution == "source":
        return vid_h
    return min(vid_h, targets.get(output_resolution, 1920))


def _build_scale_filter(
    video_format: str,
    output_resolution: str,
    vid_w: int,
    vid_h: int,
) -> str:
    """
    Build a source-aware scale filter.

    Rules:
      - Never upscale: target dimensions are clamped to the source dimensions.
      - For 9:16 vertical formats, the target is the output height.
      - For other formats, the target is the output width.
      - output_resolution == "source" preserves the original resolution.
    """
    # Map output resolution labels to pixel targets (height for vertical, width for others)
    vertical_targets = {"1080": 1920, "720": 1280, "480": 854}
    landscape_targets = {"1080": 1920, "720": 1280, "480": 854}

    is_vertical = video_format in (
        "vertical-crop", "vertical-pad", "vertical-blur",
        "vertical-speaker", "vertical-speaker-blur",
    )

    if is_vertical:
        target_h = vertical_targets.get(output_resolution, vid_h)
        # Clamp to source height so we never upscale
        safe_h = min(vid_h, target_h)
        if safe_h <= 0:
            safe_h = target_h if target_h > 0 else vid_h
        # For exact resolution, use force_original_aspect_ratio=disable;
        # for source-aware we keep aspect ratio and let crop/pad handle the rest.
        if output_resolution == "source":
            return f"scale=-2:{safe_h}:force_original_aspect_ratio=disable"
        return f"scale=1080:{safe_h}:force_original_aspect_ratio=disable"

    # Landscape / original
    target_w = landscape_targets.get(output_resolution, vid_w)
    safe_w = min(vid_w, target_w)
    if safe_w <= 0:
        safe_w = target_w if target_w > 0 else vid_w
    return f"scale={safe_w}:-2:force_original_aspect_ratio=disable"


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
    subtitle_style: str = "standard",
    video_format: str = "original",
    bgm_type: str = "none",
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
    auto_broll: bool = False,
    transcription_source: str = "auto",
    whisper_model: str = "base",
    download_resolution: str = "best",
    output_resolution: str = "1080",
    output_quality: str = "standard",
    moment_index: int = 0,
):
    """
    Full pipeline: download → (download subtitles) → cut → (embed subtitles) → cleanup.
    Runs in a background thread.
    """
    os.makedirs(output_dir, exist_ok=True)
    if moment_index:
        _update_task(task_id, moment_index=moment_index)

    _append_log(
        task_id,
        f"[CONFIG] Resolusi unduhan: {download_resolution}, "
        f"resolusi output: {output_resolution}, kualitas: {output_quality}",
    )

    output_filename  = f"clip_{task_id}.mp4"
    output_path      = os.path.join(output_dir, output_filename)
    temp_cut_path    = os.path.join(output_dir, f"_tmpcut_{task_id}.mp4")
    temp_hook_sub_path = os.path.join(output_dir, f"_hook_{task_id}.srt")
    temp_tracking_cut  = os.path.join(output_dir, f"_trackcut_{task_id}.mp4")

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
                        sys.executable, "-m", "yt_dlp", "--js-runtimes", "node",
                        "--write-auto-sub", "--write-sub",
                        "--sub-lang", subtitle_lang,
                        "--convert-subs", "srt",
                        "--skip-download",
                        "--no-check-certificates",
                        "--output", sub_dl_path,
                        "--no-playlist",
                    ]
                    if cookies:
                        sub_cmd += ["--cookies", cookies]
                    sub_cmd.append(url)
                    runner.run(
                        task_id, sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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

                primary_fmt, fallback_fmt = _build_ytdlp_formats(download_resolution)
                _append_log(task_id, f"[>>] Format unduhan: {primary_fmt}")

                download_attempts = [
                    (
                        "format utama (kualitas tinggi)",
                        [],
                        primary_fmt,
                    ),
                    (
                        "fallback web",
                        ["--extractor-args", "youtube:player_client=web"],
                        fallback_fmt,
                    ),
                ]

                output_lines = []
                for attempt_idx, (label, extra_args, fmt) in enumerate(download_attempts):
                    # Bersihkan file .part yang mungkin expired/corrupt sebelum retry
                    if attempt_idx > 0:
                        for f in os.listdir(output_dir):
                            if f.startswith(f"_cache_{video_id}") and f.endswith(".part"):
                                try:
                                    os.remove(os.path.join(output_dir, f))
                                except OSError:
                                    pass

                    yt_dlp_cmd = [
                        sys.executable, "-m", "yt_dlp", "--js-runtimes", "node",
                        *extra_args,
                        "--format", fmt,
                        "--merge-output-format", "mp4",
                        "--output", cache_video_path,
                        "--no-check-certificates",
                        "--no-playlist",
                        "--progress",
                        "--newline",
                        "--retries", "10",
                        "--fragment-retries", "10",
                    ]

                    if cookies:
                        yt_dlp_cmd += ["--cookies", cookies]

                    yt_dlp_cmd.append(url)
                    _append_log(task_id, f"[>>] Percobaan {attempt_idx + 1}/{len(download_attempts)} ({label}): {' '.join(yt_dlp_cmd)}")

                    proc = runner.TrackedPopen(
                        task_id, yt_dlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace"
                    )

                    output_lines = []
                    try:
                        while True:
                            line = proc.stdout.readline()
                            if not line and proc.poll() is not None:
                                break
                            if runner.is_cancelled(task_id):
                                break
                            if line:
                                line = line.strip()
                                output_lines.append(line)
                                # Filter output for progress
                                if "[download]" in line and "%" in line:
                                    m = re.search(r"\[download\]\s+([\d.]+)%", line)
                                    if m:
                                        _update_task(task_id, progress=int(5 + float(m.group(1)) * 0.55))
                                elif "[youtube]" in line or "ERROR:" in line or "[Merger]" in line or "WARNING:" in line:
                                    _append_log(task_id, line)
                    finally:
                        try:
                            if proc.returncode is None:
                                proc.terminate()
                                proc.wait(timeout=2.0)
                        except Exception:
                            pass
                        runner.unregister_proc(task_id)

                    if proc.returncode != 0:
                        _append_log(task_id, f"⚠️ Percobaan {attempt_idx + 1} gagal (exit {proc.returncode}), memverifikasi file...")

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

                    if downloaded_file:
                        _append_log(task_id, f"✅ Unduhan berhasil dengan {label}.")
                        break

                    # Jika bukan karena 403, tidak perlu retry fallback
                    attempt_log = "\n".join(output_lines[-30:])
                    if "HTTP Error 403" not in attempt_log and "unable to download video data" not in attempt_log:
                        break
                    _append_log(task_id, "⚠️ Format diblokir (403), mencoba fallback...")

                if not downloaded_file:
                    last_log = "\n".join(output_lines[-30:])
                    _append_log(task_id, f"[yt-dlp output]\n{last_log}")
                    raise RuntimeError(
                        f"Gagal mengunduh video (Exit Code: {proc.returncode}).\n"
                        f"YouTube mungkin memblokir akses, URL tidak valid, atau ffmpeg/merger gagal.\n"
                        f"Log yt-dlp:\n{last_log}"
                    )

                # Download subtitle to task-specific path (not shared cache)
                if subtitle_enabled:
                    _append_log(task_id, "[>>] Mengunduh subtitle (task-specific)...")
                    first_lang = subtitle_lang.split(",")[0].strip()
                    sub_dl_path = os.path.join(output_dir, f"_sub_dl_{task_id}")
                    sub_cmd = [
                        sys.executable, "-m", "yt_dlp", "--js-runtimes", "node",
                        "--write-auto-sub", "--write-sub",
                        "--sub-lang", subtitle_lang,
                        "--convert-subs", "srt",
                        "--skip-download",
                        "--no-check-certificates",
                        "--output", sub_dl_path,
                        "--no-playlist",
                    ]
                    if cookies:
                        sub_cmd += ["--cookies", cookies]
                    sub_cmd.append(url)
                    runner.run(
                        task_id, sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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

                # ── Sentence-aware boundary snapping ─────────────────────────
                # Try to snap the requested start/end to full sentence boundaries
                # so the clip contains complete context instead of cutting
                # mid-sentence.
                try:
                    with open(subtitle_file, "r", encoding="utf-8", errors="replace") as sfh:
                        raw_srt = sfh.read()
                    snap_segments = _parse_srt(raw_srt)
                    # We intentionally do not clamp to the original end time here:
                    # sentence snapping may need to extend slightly beyond the
                    # requested end to finish the current sentence. FFmpeg will
                    # stop at EOF if the result exceeds the video.
                    snapped_start, snapped_end = _snap_to_sentence_boundaries(
                        snap_segments,
                        _parse_seconds(start),
                        _parse_seconds(end),
                        duration_secs=None,
                        min_duration=1.0,
                        max_duration=float('inf'),
                    )
                    if abs(snapped_start - _parse_seconds(start)) > 0.5 or abs(snapped_end - _parse_seconds(end)) > 0.5:
                        _append_log(
                            task_id,
                            f"[CONTEXT] Menyesuaikan batas clip ke batas kalimat: "
                            f"{start}-{end} -> {_seconds_to_hhmmss(snapped_start)}-{_seconds_to_hhmmss(snapped_end)}"
                        )
                        start = _seconds_to_hhmmss(snapped_start)
                        end = _seconds_to_hhmmss(snapped_end)
                except Exception as e:
                    _append_log(task_id, f"[CONTEXT] Gagal snap ke batas kalimat: {e}")

                # Shift subtitle timestamps to match clip start
                shifted_sub_path = os.path.join(output_dir, f"_sub_{task_id}.srt")
                start_sec = _parse_seconds(start)
                
                # If burning subtitles, we use one-pass accurate seek which starts decoder at fast_seek_sec.
                # So the subtitle must be shifted by fast_seek_sec, not start_sec.
                # For soft subs, the output video starts exactly at start_sec, so we shift by start_sec.
                fast_seek_sec = max(0.0, start_sec - 30.0)
                sub_shift_sec = fast_seek_sec if subtitle_type == "burn" else start_sec
                
                entry_count = _shift_srt(subtitle_file, sub_shift_sec, shifted_sub_path, sub_case, subtitle_style)
                if entry_count == 0 or not _validate_srt_file(shifted_sub_path):
                    _append_log(task_id, "[!] Subtitle kosong setelah diproses (clip mungkin di luar jangkauan subtitle) — lanjut tanpa subtitle.")
                    subtitle_enabled = False
                    shifted_sub_path = None
                else:
                    _append_log(task_id, f"[CC] Timestamp disesuaikan. {entry_count} baris subtitle siap.")
            else:
                _append_log(task_id, "[!] Subtitle tidak ditemukan — mencoba alternatif...")
                # Try Whisper if requested or auto
                if downloaded_file and transcription_source in ("auto", "whisper"):
                    whisper_srt = _transcribe_with_whisper(
                        task_id, downloaded_file, output_dir,
                        model_size=whisper_model, language=subtitle_lang,
                    )
                    if whisper_srt:
                        subtitle_file = whisper_srt
                        _append_log(task_id, f"[CC] Menggunakan subtitle Whisper: {os.path.basename(subtitle_file)}")
                        # Re-enter the subtitle processing block
                        try:
                            with open(subtitle_file, "r", encoding="utf-8", errors="replace") as sfh:
                                raw_srt = sfh.read()
                            snap_segments = _parse_srt(raw_srt)
                            snapped_start, snapped_end = _snap_to_sentence_boundaries(
                                snap_segments,
                                _parse_seconds(start),
                                _parse_seconds(end),
                                duration_secs=None,
                                min_duration=1.0,
                                max_duration=float('inf'),
                            )
                            if abs(snapped_start - _parse_seconds(start)) > 0.5 or abs(snapped_end - _parse_seconds(end)) > 0.5:
                                _append_log(
                                    task_id,
                                    f"[CONTEXT] Menyesuaikan batas clip ke batas kalimat: "
                                    f"{start}-{end} -> {_seconds_to_hhmmss(snapped_start)}-{_seconds_to_hhmmss(snapped_end)}"
                                )
                                start = _seconds_to_hhmmss(snapped_start)
                                end = _seconds_to_hhmmss(snapped_end)
                        except Exception as e:
                            _append_log(task_id, f"[CONTEXT] Gagal snap ke batas kalimat: {e}")

                        shifted_sub_path = os.path.join(output_dir, f"_sub_{task_id}.srt")
                        start_sec = _parse_seconds(start)
                        fast_seek_sec = max(0.0, start_sec - 30.0)
                        sub_shift_sec = fast_seek_sec if subtitle_type == "burn" else start_sec
                        entry_count = _shift_srt(subtitle_file, sub_shift_sec, shifted_sub_path, sub_case, subtitle_style)
                        if entry_count == 0 or not _validate_srt_file(shifted_sub_path):
                            _append_log(task_id, "[!] Subtitle Whisper kosong setelah diproses — lanjut tanpa subtitle.")
                            subtitle_enabled = False
                            shifted_sub_path = None
                        else:
                            _append_log(task_id, f"[CC] Timestamp Whisper disesuaikan. {entry_count} baris subtitle siap.")
                    else:
                        _append_log(task_id, "⚠️ Whisper tidak menghasilkan subtitle — lanjut tanpa subtitle.")
                        subtitle_enabled = False
                else:
                    _append_log(task_id, "⚠️ Tidak ada sumber subtitle tersedia — lanjut tanpa subtitle.")
                    subtitle_enabled = False

        # ── Step 2b: Speaker Tracking (if vertical-speaker or vertical-speaker-blur) ─
        speaker_crop_filter = None
        vid_w, vid_h = 0, 0
        if video_format in ("vertical-speaker", "vertical-speaker-blur"):
            _update_task(task_id, status="tracking", progress=55)
            _append_log(task_id, "[TRACK] 🎯 Memulai analisis wajah untuk Speaker Tracking...")

            # First, cut the relevant segment for analysis (fast cut)
            start_sec = _parse_seconds(start)
            try:
                duration = _parse_seconds(end) - start_sec
                duration_ff = str(max(duration, 1.0))
            except Exception:
                duration_ff = _seconds_to_ffmpeg(end)

            tracking_cut_cmd = [
                "ffmpeg", "-y",
                "-ss", _seconds_to_ffmpeg(start),
                "-i", downloaded_file,
                "-t", duration_ff,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                temp_tracking_cut,
            ]
            _append_log(task_id, "[TRACK] Memotong segmen untuk analisis...")
            _run_ffmpeg(task_id, tracking_cut_cmd, start=55, end=58)

            if os.path.isfile(temp_tracking_cut):
                # Get video dimensions
                vid_w, vid_h, vid_fps = face_tracker.get_video_dimensions(temp_tracking_cut)

                if vid_w > 0 and vid_h > 0:
                    # Analyze faces
                    _update_task(task_id, progress=60)
                    face_data = face_tracker.analyze_faces(
                        temp_tracking_cut,
                        sample_fps=3.0,
                        log_fn=lambda msg: _append_log(task_id, msg),
                    )

                    faces_detected = sum(
                        1 for f in face_data if f.get("center_x") is not None
                    )

                    if faces_detected > 0:
                        # Generate crop data
                        _update_task(task_id, progress=70)
                        crop_data = face_tracker.generate_crop_data(
                            face_data,
                            vid_w,
                            vid_h,
                            target_ratio=9.0 / 16.0,
                            smoothing=0.12,
                            log_fn=lambda msg: _append_log(task_id, msg),
                        )

                        # Build crop filter string
                        speaker_crop_filter = face_tracker.build_crop_filter_string(
                            crop_data, vid_w, vid_h
                        )
                        _append_log(
                            task_id,
                            f"[TRACK] ✅ Speaker tracking siap! "
                            f"{faces_detected}/{len(face_data)} frame dengan wajah terdeteksi.",
                        )
                    else:
                        _append_log(
                            task_id,
                            "[TRACK] ⚠️ Tidak ada wajah terdeteksi — menggunakan center crop.",
                        )
                else:
                    _append_log(
                        task_id,
                        "[TRACK] ⚠️ Gagal membaca dimensi video — menggunakan center crop.",
                    )
            else:
                _append_log(
                    task_id,
                    "[TRACK] ⚠️ Gagal memotong segmen untuk analisis — menggunakan center crop.",
                )

        # Ensure we have video dimensions for source-aware scaling
        if not (vid_w and vid_h) and downloaded_file and os.path.isfile(downloaded_file):
            try:
                vid_w, vid_h, _ = face_tracker.get_video_dimensions(downloaded_file)
                _append_log(task_id, f"[DIMENSI] Sumber: {vid_w}x{vid_h}")
            except Exception as e:
                _append_log(task_id, f"[DIMENSI] Gagal membaca dimensi: {e}")
                vid_w, vid_h = 0, 0
        if not (vid_w and vid_h):
            vid_w, vid_h = 1920, 1080

        broll_events = []
        if auto_broll and has_sub_file and subtitle_type == "burn":
            broll_events = _detect_broll_timestamps(shifted_sub_path)
            if broll_events:
                _append_log(task_id, f"[B-ROLL] Terdeteksi B-Roll! Overlay {broll_events[0]['file']} pada detik {broll_events[0]['start']:.1f}")

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
                if acc_seek_sec > 0:
                    ffmpeg_cmd.extend(["-ss", f"{acc_seek_sec:.3f}"])
                ffmpeg_cmd.extend(["-t", duration_ff])
            else:
                ffmpeg_cmd = ["ffmpeg", "-y", "-i", temp_cut_path]

            if has_sub_file and subtitle_type == "soft":
                ffmpeg_cmd.extend(["-i", shifted_sub_path])
                ffmpeg_cmd.extend(["-map", "1:s:0"])  # Map subtitle from second input

            vf_filters = []
            target_h = _vertical_target_height(output_resolution, vid_h)
            target_h_fg = max(int(target_h * 0.9), 360)  # foreground ~90% height for blur edge effect
            _append_log(task_id, f"[FORMAT] Target vertical height: {target_h}px (source height: {vid_h}px)")
            if video_format == "vertical-crop":
                vf_filters.append("crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'")
                vf_filters.append(f"scale=-2:{target_h}:force_original_aspect_ratio=disable")
                vf_filters.append("setsar=1")
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Crop Center)")
            elif video_format == "vertical-pad":
                vf_filters.append("pad='max(iw,ih*9/16)':'max(ih,iw*16/9)':(ow-iw)/2:(oh-ih)/2:black")
                vf_filters.append(f"scale=-2:{target_h}:force_original_aspect_ratio=disable")
                vf_filters.append("setsar=1")
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Pad Black Bars)")
            elif video_format == "vertical-blur":
                # Background: center 9:16 crop blurred and scaled to target height.
                # Foreground: same center crop scaled slightly smaller so blur edges show.
                vf_filters.append(
                    f"split=2[bg][fg];"
                    f"[bg]crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=-2:{target_h}:force_original_aspect_ratio=disable,boxblur=20:5[bg_blur];"
                    f"[fg]crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=-2:{target_h_fg}[fg_scaled];"
                    f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2"
                )
                _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Blur Background)")
            elif video_format == "vertical-speaker":
                if speaker_crop_filter:
                    vf_filters.append(speaker_crop_filter)
                    # Scale to target height (source-aware)
                    vf_filters.append(f"scale=-2:{target_h}:force_original_aspect_ratio=disable")
                    vf_filters.append("setsar=1")
                    _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (🎯 Speaker Tracking)")
                else:
                    # Fallback to center crop when tracking failed
                    vf_filters.append("crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'")
                    vf_filters.append(f"scale=-2:{target_h}:force_original_aspect_ratio=disable")
                    vf_filters.append("setsar=1")
                    _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Center Crop — fallback)")
            elif video_format == "vertical-speaker-blur":
                target_h_fg = max(int(target_h * 0.9), 360)
                # Speaker Tracking + Blur Background
                # The speaker_crop_filter contains the dynamic crop expression from face tracking.
                # We use it as the foreground, and a blurred center crop as background.
                # This is a complex filter, so we build a filter_complex-compatible string.
                if speaker_crop_filter:
                    vf_filters.append(
                        f"split=2[bg][fg];"
                        f"[bg]crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=-2:{target_h}:force_original_aspect_ratio=disable,boxblur=20:5[bg_blur];"
                        f"[fg]{speaker_crop_filter},scale=-2:{target_h_fg}[fg_tracked];"
                        f"[bg_blur][fg_tracked]overlay=(W-w)/2:(H-h)/2"
                    )
                    _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (🎯 Speaker Tracking + Blur Background)")
                else:
                    # Fallback to standard blur background (center crop) when tracking failed
                    vf_filters.append(
                        f"split=2[bg][fg];"
                        f"[bg]crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=-2:{target_h}:force_original_aspect_ratio=disable,boxblur=20:5[bg_blur];"
                        f"[fg]crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=-2:{target_h_fg}[fg_scaled];"
                        f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2"
                    )
                    _append_log(task_id, "[FORMAT] Mengubah ke 9:16 (Blur Background — fallback)")
            elif video_format == "original" and output_resolution != "source":
                # Optional downscale for original format
                vf_filters.append(_build_scale_filter(video_format, output_resolution, vid_w, vid_h))
                _append_log(task_id, f"[FORMAT] Original format dengan batas resolusi {output_resolution}")

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

                margin_v = "90" if align == "2" else "10"

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
                    f"Shadow={sub_shadow},"
                    f"MarginV={margin_v}"
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

            bgm_file = os.path.join("bgm", f"{bgm_type}.mp3")
            has_bgm = bgm_type != "none" and os.path.isfile(bgm_file)
            
            # Base video filter logic
            video_filter_str = ",".join(vf_filters) if vf_filters else ""
            filter_complex = ""
            map_v = "[v]" if video_filter_str else "0:v:0"
            
            # Input index tracker
            next_input_idx = 1
            if has_sub_file and subtitle_type == "soft":
                next_input_idx += 1
            
            # ── B-Roll Overlay Logic ──
            if broll_events:
                broll = broll_events[0]
                broll_input_idx = next_input_idx
                ffmpeg_cmd.extend(["-i", broll["file"]])
                next_input_idx += 1
                
                # Setup complex filter for overlay
                # broll scaled and overlaid over main video [v] or 0:v:0
                broll_v_in = f"{map_v}"
                filter_complex += f"[{broll_input_idx}:v:0]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[b_scaled];"
                if video_filter_str:
                    filter_complex = f"[0:v:0]{video_filter_str}[main_v];" + filter_complex
                    broll_v_in = "[main_v]"
                
                filter_complex += f"{broll_v_in}[b_scaled]overlay=0:0:enable='between(t,{broll['start']},{broll['end']})'[v];"
                map_v = "[v]"
            else:
                if video_filter_str:
                    filter_complex += f"[0:v:0]{video_filter_str}[v];"

            # ── BGM Audio Mixing Logic ──
            if has_bgm:
                bgm_input_idx = next_input_idx
                ffmpeg_cmd.extend(["-stream_loop", "-1", "-i", bgm_file])
                _append_log(task_id, f"[AUDIO] Mencampur lagu latar ({bgm_type})...")
                
                filter_complex += f"[0:a:0]volume=1.0[a1];[{bgm_input_idx}:a:0]volume=0.1[a2];[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]"
                map_a = "[a]"
            else:
                map_a = "0:a:0"
                # If filter_complex ends with a semicolon and no audio mixing, strip it to keep it valid
                if filter_complex.endswith(";"):
                    pass # it's fine, we will just use [v] and 0:a:0
            
            if filter_complex:
                filter_complex = filter_complex.strip(";")
                ffmpeg_cmd.extend(["-filter_complex", filter_complex])
            # Always map both video and audio streams
            ffmpeg_cmd.extend(["-map", map_v, "-map", map_a])

            quality_profile = _get_quality_profile(output_quality)
            _append_log(
                task_id,
                f"[QUALITY] Profil: {output_quality} — CRF {quality_profile['crf']}, "
                f"preset {quality_profile['preset']}, audio {quality_profile['audio_bitrate']}"
            )

            ffmpeg_cmd.extend([
                "-c:v", "libx264", "-preset", quality_profile["preset"], "-crf", quality_profile["crf"],
                "-pix_fmt", "yuv420p",          # Force 8-bit 4:2:0 — browser-compatible
                "-profile:v", "high", "-level", "4.0",  # Max browser compat H.264 profile
                "-c:a", "aac", "-b:a", quality_profile["audio_bitrate"],  # Re-encode audio to AAC
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

        # ── Step 4: Virality scoring & thumbnail generation ────────────────────
        try:
            start_sec = _parse_seconds(start)
            end_sec = _parse_seconds(end)

            # Collect transcript segments inside the clip window for scoring
            clip_segments = []
            if subtitle_enabled and shifted_sub_path and os.path.isfile(shifted_sub_path):
                # shifted_sub_path timestamps are already relative to clip start
                clip_segments = _parse_srt(open(shifted_sub_path, "r", encoding="utf-8", errors="replace").read())
            elif subtitle_enabled and subtitle_file and os.path.isfile(subtitle_file):
                clip_segments = _extract_clip_segments(subtitle_file, start_sec, end_sec)

            score_info = virality.score_moment(
                start_sec=start_sec,
                end_sec=end_sec,
                hook_title=hook_title or "",
                transcript_segments=clip_segments,
            )
            _append_log(
                task_id,
                f"[VIRALITY] Skor: {score_info['score']}/100 ({score_info['badge']}) — {score_info['reason']}"
            )

            # Generate thumbnail image for the clip
            thumb_files = thumbnail.generate_thumbnails(
                video_path=output_path,
                hook_title=hook_title or "CLIP",
                output_dir=output_dir,
                task_id=task_id,
                video_format=video_format,
                num_variants=1,
            )
            thumb_file = os.path.basename(thumb_files[0]) if thumb_files else None
            if thumb_file:
                _append_log(task_id, f"[THUMBNAIL] Thumbnail tersimpan: {thumb_file}")

            _update_task(task_id, status="uploading", progress=97)
            thumbnail_path = os.path.join(output_dir, thumb_file) if thumb_file else None
            cloud_storage.persist_task_assets(task_id, output_path, thumbnail_path)
            _update_task(
                task_id, status="done", progress=100, output_file=output_filename,
                virality_score=score_info["score"], virality_reason=score_info["reason"],
                thumbnail_file=thumb_file,
            )
        except Exception as meta_err:
            _append_log(task_id, f"[VIRALITY/STORAGE] Gagal menyimpan metadata: {meta_err}")
            if os.path.isfile(output_path):
                try:
                    _update_task(task_id, status="uploading", progress=97)
                    cloud_storage.persist_task_assets(task_id, output_path)
                    _update_task(task_id, status="done", progress=100, output_file=output_filename)
                except Exception as storage_err:
                    raise RuntimeError(f"Upload cloud gagal: {storage_err}") from storage_err

        # ── Step 5: Cleanup ──────────────────────────────────────────────────
        # Delete ONLY task-specific temp files. The video cache (_cache_{video_id}.mp4)
        # is intentionally kept for reuse on subsequent clips of the same video.
        temp_files_to_delete = [
            temp_cut_path,          # _tmpcut_{task_id}.mp4
            shifted_sub_path,       # _sub_{task_id}.srt  (shifted subtitle)
            temp_hook_sub_path,     # _hook_{task_id}.srt
            temp_tracking_cut,      # _trackcut_{task_id}.mp4  (speaker tracking analysis)
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
    with runner.TrackedPopen(
        task_id,
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
    subtitle_style: str = "standard",
    video_format: str = "original",
    bgm_type: str = "none",
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
    auto_broll: bool = False,
    transcription_source: str = "auto",
    whisper_model: str = "base",
    download_resolution: str = "best",
    output_resolution: str = "1080",
    output_quality: str = "standard",
):
    """
    Menjalankan proses pemotongan di thread terpisah.
    Sekarang menggunakan task queue daripada thread satu per task.
    """
    import task_queue as q_module
    kwargs = {
        "subtitle_enabled": subtitle_enabled,
        "subtitle_lang": subtitle_lang,
        "subtitle_type": subtitle_type,
        "subtitle_auto": subtitle_auto,
        "subtitle_position": subtitle_position,
        "sub_fontsize": sub_fontsize,
        "sub_case": sub_case,
        "sub_bold": sub_bold,
        "sub_italic": sub_italic,
        "sub_underline": sub_underline,
        "subtitle_style": subtitle_style,
        "video_format": video_format,
        "bgm_type": bgm_type,
        "sub_primary_color": sub_primary_color,
        "sub_outline_color": sub_outline_color,
        "sub_back_color": sub_back_color,
        "sub_back_alpha": sub_back_alpha,
        "sub_border_style": sub_border_style,
        "sub_outline_width": sub_outline_width,
        "sub_shadow": sub_shadow,
        "hook_title": hook_title,
        "hook_fontsize": hook_fontsize,
        "hook_preset": hook_preset,
        "hook_position": hook_position,
        "cookies": cookies,
        "auto_broll": auto_broll,
        "transcription_source": transcription_source,
        "whisper_model": whisper_model,
        "download_resolution": download_resolution,
        "output_resolution": output_resolution,
        "output_quality": output_quality,
    }
    q_module.submit_task(task_id, url, start, end, output_dir, kwargs)
    return None
