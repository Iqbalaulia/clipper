"""
whisper_engine.py — Local audio transcription using faster-whisper (optional).

Falls back gracefully if faster-whisper is not installed, so the rest of the
app still works with YouTube auto-generated subtitles.
"""

import os
import re
import json
import logging
from typing import List, Optional

logger = logging.getLogger("clipper")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WHISPER_MODEL_DIR = os.path.join(BASE_DIR, "models")
WHISPER_CACHE_DIR = os.path.join(BASE_DIR, "data", "whisper_cache")

os.makedirs(WHISPER_MODEL_DIR, exist_ok=True)
os.makedirs(WHISPER_CACHE_DIR, exist_ok=True)


class WhisperNotAvailable(Exception):
    """Raised when faster-whisper is not installed or fails to load."""
    pass


def _get_whisper_module():
    """Lazy import faster-whisper."""
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError:
        return None


def _seconds_to_srt_ts(seconds: float) -> str:
    """Convert seconds to SRT timestamp format HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _cache_path(video_path: str, model_size: str, language: Optional[str]) -> str:
    """Return a stable cache path for a transcription result."""
    import hashlib
    key = f"{video_path}|{model_size}|{language or 'auto'}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return os.path.join(WHISPER_CACHE_DIR, f"{digest}.json")


def _load_cached(video_path: str, model_size: str, language: Optional[str]) -> Optional[List[dict]]:
    """Load cached transcription if available and newer than the video."""
    path = _cache_path(video_path, model_size, language)
    if not os.path.isfile(path):
        return None
    try:
        video_mtime = os.path.getmtime(video_path)
        cache_mtime = os.path.getmtime(path)
        if cache_mtime < video_mtime:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cached(video_path: str, model_size: str, language: Optional[str], segments: List[dict]) -> None:
    path = _cache_path(video_path, model_size, language)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to cache whisper transcription: %s", e)


def transcribe(
    video_path: str,
    model_size: str = "base",
    language: Optional[str] = None,
    device: str = "cpu",
    compute_type: str = "int8",
    vad_filter: bool = True,
) -> List[dict]:
    """
    Transcribe a video file using faster-whisper and return segments in the
    same format as the subtitle parser: {start, end, text}.

    Args:
        video_path: Path to the video file.
        model_size: Whisper model size (tiny, base, small, medium, large-v1/v2/v3).
        language: Optional language code (e.g. 'id', 'en'). Auto-detect if None.
        device: 'cpu' or 'cuda'.
        compute_type: 'int8', 'float16', etc.
        vad_filter: Whether to use voice activity detection to filter non-speech.

    Returns:
        List of segments with start/end in seconds and text.
    """
    WhisperModel = _get_whisper_module()
    if WhisperModel is None:
        raise WhisperNotAvailable(
            "faster-whisper tidak terinstall. Jalankan: pip install faster-whisper"
        )

    cached = _load_cached(video_path, model_size, language)
    if cached is not None:
        logger.info("Using cached Whisper transcription for %s", video_path)
        return cached

    logger.info(
        "Running Whisper transcription on %s (model=%s, lang=%s, device=%s)",
        video_path, model_size, language or "auto", device,
    )

    try:
        model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=WHISPER_MODEL_DIR,
        )
        segments_iter, info = model.transcribe(
            video_path,
            language=language,
            vad_filter=vad_filter,
            condition_on_previous_text=False,
        )
        logger.info("Whisper detected language: %s (probability %.2f)", info.language, info.language_probability)
    except Exception as e:
        logger.exception("Whisper transcription failed")
        raise WhisperNotAvailable(f"Gagal menjalankan Whisper: {e}")

    segments = []
    for seg in segments_iter:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })

    _save_cached(video_path, model_size, language, segments)
    logger.info("Whisper transcription complete: %d segments", len(segments))
    return segments


def segments_to_srt(segments: List[dict]) -> str:
    """Convert Whisper segments to SRT format."""
    blocks = []
    for i, seg in enumerate(segments, start=1):
        start_ts = _seconds_to_srt_ts(seg["start"])
        end_ts = _seconds_to_srt_ts(seg["end"])
        blocks.append(f"{i}\n{start_ts} --> {end_ts}\n{seg['text']}\n")
    return "\n".join(blocks)


def transcribe_to_srt(
    video_path: str,
    model_size: str = "base",
    language: Optional[str] = None,
) -> str:
    """Convenience wrapper: transcribe and return an SRT string."""
    segments = transcribe(video_path, model_size=model_size, language=language)
    return segments_to_srt(segments)


def is_available() -> bool:
    """Return True if faster-whisper can be imported."""
    return _get_whisper_module() is not None


def list_models() -> List[str]:
    """Return available faster-whisper model sizes."""
    return ["tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"]
