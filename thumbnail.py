"""
thumbnail.py — Automatic thumbnail generation for clips.

Generates a static thumbnail image by extracting a keyframe from the clip and
overlaying the hook title. Uses FFmpeg drawtext so no extra Python dependencies
(Pillow/ImageMagick) are required.

Public API:
    generate_thumbnails(video_path, hook_title, output_dir, task_id,
                        video_format="original", num_variants=1,
                        timestamp_pct=0.2)
"""

import os
import re
import subprocess
from typing import Optional


# Font candidates searched in OS font directories
_FONT_CANDIDATES = {
    "impact": [
        "C:/Windows/fonts/impact.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
    "arial": [
        "C:/Windows/fonts/arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ],
}


def _find_font(variant: str = "impact") -> Optional[str]:
    """Return the first existing font path for the requested variant."""
    for path in _FONT_CANDIDATES.get(variant, _FONT_CANDIDATES["impact"]):
        if path and os.path.isfile(path):
            return path
    # Last-ditch: let FFmpeg use its default font
    return None


def _escape_text(text: str) -> str:
    """Escape characters that break FFmpeg drawtext."""
    # Replace single colon with escaped version inside drawtext text
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    return text


def _detect_orientation(video_path: str) -> str:
    """Return 'portrait' or 'landscape' based on the input video."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        video_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(",")
            if len(parts) >= 2:
                w, h = int(parts[0]), int(parts[1])
                return "portrait" if h > w else "landscape"
    except Exception:
        pass
    return "landscape"


def _get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return float(r.stdout.strip())
    except Exception:
        pass
    return 0.0


def _split_title_lines(title: str, max_chars_per_line: int = 12) -> list[str]:
    """Split a long hook title into 2-3 lines for thumbnail readability."""
    words = title.split()
    if not words:
        return [""]

    lines = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars_per_line:
            current = f"{current} {word}".strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    # If we have more than 3 lines, merge into 3 balanced lines
    if len(lines) > 3:
        per_line = max(1, len(words) // 3)
        lines = []
        for i in range(0, len(words), per_line):
            chunk = words[i:i + per_line]
            lines.append(" ".join(chunk))
            if len(lines) >= 3:
                lines[-1] = " ".join(words[i:])
                break
    return lines[:3]


def _build_drawtext_filter(
    title: str,
    font_path: Optional[str],
    orientation: str,
    variant_index: int = 0,
) -> str:
    """Build an FFmpeg drawtext filter string for the thumbnail."""
    lines = _split_title_lines(title)
    line_count = len(lines)

    # Choose style based on variant index
    styles = [
        {"primary": "yellow", "outline": "black", "box": 1, "boxcolor": "black@0.5"},
        {"primary": "white", "outline": "red", "box": 0, "boxcolor": "transparent"},
        {"primary": "white", "outline": "black", "box": 1, "boxcolor": "blue@0.6"},
    ]
    style = styles[variant_index % len(styles)]

    base_fontsize = 64 if orientation == "portrait" else 56
    line_spacing = base_fontsize + 18
    y_start = f"(h-text_h)/2 - {(line_count - 1) * line_spacing // 2}"

    font_arg = f"fontfile='{font_path}'" if font_path else ""
    filters = []
    for i, line in enumerate(lines):
        escaped = _escape_text(line)
        y_pos = f"{y_start}+{i * line_spacing}"
        box_arg = f"box=1:boxcolor={style['boxcolor']}:boxborderw=12" if style.get("box") else "box=0"
        filter_str = (
            f"drawtext=text='{escaped}'"
            f":{font_arg}"
            f":fontsize={base_fontsize}"
            f":fontcolor={style['primary']}"
            f":borderw=4"
            f":bordercolor={style['outline']}"
            f":x=(w-text_w)/2"
            f":y={y_pos}"
            f":{box_arg}"
        )
        filters.append(filter_str)

    return ",".join(filters)


def generate_thumbnails(
    video_path: str,
    hook_title: str,
    output_dir: str,
    task_id: str,
    video_format: str = "original",
    num_variants: int = 1,
    timestamp_pct: float = 0.2,
) -> list[str]:
    """
    Generate one or more thumbnail images for a clip.

    Args:
        video_path: path to the rendered clip
        hook_title: hook title text to overlay
        output_dir: directory to write thumbnails into
        task_id: task id used in the output filename
        video_format: used to pick default orientation (vertical formats -> portrait)
        num_variants: number of style variants to generate (default 1)
        timestamp_pct: position in the clip to extract the frame (0.0 - 1.0)

    Returns:
        List of generated thumbnail file paths (may be empty on failure).
    """
    if not os.path.isfile(video_path):
        return []
    if not hook_title:
        hook_title = "WATCH THIS"

    os.makedirs(output_dir, exist_ok=True)

    duration = _get_video_duration(video_path)
    timestamp = max(0.0, min(duration * timestamp_pct, duration - 0.1)) if duration > 0 else 0.0

    orientation = "portrait" if video_format in (
        "vertical-crop", "vertical-pad", "vertical-blur",
        "vertical-speaker", "vertical-speaker-blur",
    ) else _detect_orientation(video_path)

    # Target output dimensions
    if orientation == "portrait":
        out_w, out_h = 1080, 1920
    else:
        out_w, out_h = 1280, 720

    font_path = _find_font("impact")
    generated = []

    for variant in range(num_variants):
        suffix = chr(ord("a") + variant)
        out_file = os.path.join(output_dir, f"thumb_{task_id}_{suffix}.jpg")

        vf = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
        drawtext = _build_drawtext_filter(hook_title, font_path, orientation, variant)
        if drawtext:
            vf = f"{vf},{drawtext}"

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{timestamp:.3f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            "-vf", vf,
            out_file,
        ]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
                generated.append(out_file)
            else:
                # FFmpeg drawtext can fail on missing font; try fallback without fontfile
                if font_path and "font" in (r.stderr or "").lower():
                    vf_no_font = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}"
                    drawtext_no_font = _build_drawtext_filter(hook_title, None, orientation, variant)
                    if drawtext_no_font:
                        vf_no_font = f"{vf_no_font},{drawtext_no_font}"
                    cmd_fallback = [
                        "ffmpeg", "-y",
                        "-ss", f"{timestamp:.3f}",
                        "-i", video_path,
                        "-frames:v", "1",
                        "-q:v", "2",
                        "-vf", vf_no_font,
                        out_file,
                    ]
                    r2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=60)
                    if r2.returncode == 0 and os.path.isfile(out_file) and os.path.getsize(out_file) > 0:
                        generated.append(out_file)
        except Exception:
            pass

    return generated
