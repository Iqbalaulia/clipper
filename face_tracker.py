"""
face_tracker.py — Face Detection & Dynamic Crop Coordinate Generator

Uses MediaPipe Face Detection + OpenCV to analyze video frames,
detect the most prominent face, and generate smooth crop coordinates
for FFmpeg's sendcmd filter — enabling "Speaker Tracking" auto-reframe.
"""

import os
import re
import math
import tempfile
import urllib.request
from typing import List, Tuple, Optional, Callable


def analyze_faces(
    video_path: str,
    sample_fps: float = 3.0,
    min_detection_confidence: float = 0.3,
    log_fn: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """
    Analyze video and detect faces at a sampled frame rate.

    Args:
        video_path: Path to the input video file.
        sample_fps: How many frames per second to analyze (lower = faster).
        min_detection_confidence: MediaPipe detection confidence threshold.
        log_fn: Optional callback for progress logging.

    Returns:
        List of dicts: [
            {
                "timestamp": float,     # seconds
                "center_x": float,      # 0.0 - 1.0 (normalized)
                "center_y": float,      # 0.0 - 1.0 (normalized)
                "width": float,         # 0.0 - 1.0 (normalized face width)
                "height": float,        # 0.0 - 1.0 (normalized face height)
            }, ...
        ]
        Returns empty list if no faces detected or on error.
    """
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    if not os.path.isfile(video_path):
        if log_fn:
            log_fn(f"[TRACK] File video tidak ditemukan: {video_path}")
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        if log_fn:
            log_fn("[TRACK] Gagal membuka file video untuk analisis wajah.")
        return []

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    if video_width == 0 or video_height == 0:
        cap.release()
        if log_fn:
            log_fn("[TRACK] Resolusi video tidak valid.")
        return []

    # Prepare model
    model_path = os.path.join(os.path.dirname(__file__), 'blaze_face_short_range.tflite')
    if not os.path.exists(model_path):
        if log_fn:
            log_fn("[TRACK] Mengunduh model pendeteksi wajah...")
        url = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
        try:
            urllib.request.urlretrieve(url, model_path)
        except Exception as e:
            if log_fn:
                log_fn(f"[TRACK] Gagal mengunduh model: {e}")
            return []

    # Calculate frame skip interval
    frame_interval = max(1, int(video_fps / sample_fps))
    total_to_analyze = total_frames // frame_interval if total_frames > 0 else 0

    if log_fn:
        log_fn(
            f"[TRACK] Menganalisis video: {video_width}x{video_height} @ {video_fps:.1f}fps, "
            f"sampling setiap {frame_interval} frame (~{sample_fps:.1f} fps analisis)"
        )

    face_data = []
    frame_idx = 0
    analyzed_count = 0
    faces_found_count = 0

    base_options = python.BaseOptions(model_asset_path=model_path)
    options = vision.FaceDetectorOptions(
        base_options=base_options,
        min_detection_confidence=min_detection_confidence
    )
    detector = vision.FaceDetector.create_from_options(options)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            timestamp = frame_idx / video_fps

            # Convert BGR to RGB for MediaPipe
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            results = detector.detect(mp_image)

            if results.detections:
                # Pick the largest face (by bounding box area)
                best_face = max(
                    results.detections,
                    key=lambda d: d.bounding_box.width * d.bounding_box.height
                )

                bbox = best_face.bounding_box
                
                # Convert from pixel coords to normalized (0.0-1.0)
                norm_w = bbox.width / video_width
                norm_h = bbox.height / video_height
                cx = (bbox.origin_x + bbox.width / 2.0) / video_width
                cy = (bbox.origin_y + bbox.height / 2.0) / video_height

                # Clamp to valid range
                cx = max(0.0, min(1.0, cx))
                cy = max(0.0, min(1.0, cy))

                face_data.append(
                    {
                        "timestamp": timestamp,
                        "center_x": cx,
                        "center_y": cy,
                        "width": norm_w,
                        "height": norm_h,
                    }
                )
                faces_found_count += 1
            else:
                # No face detected at this timestamp — append None marker
                face_data.append(
                    {
                        "timestamp": timestamp,
                        "center_x": None,
                        "center_y": None,
                        "width": 0,
                        "height": 0,
                    }
                )

            analyzed_count += 1

            # Progress logging every ~30 analyzed frames
            if log_fn and analyzed_count % 30 == 0 and total_to_analyze > 0:
                pct = min(100, int(analyzed_count / total_to_analyze * 100))
                log_fn(
                    f"[TRACK] Analisis wajah: {pct}% "
                    f"({analyzed_count}/{total_to_analyze} frame, "
                    f"{faces_found_count} wajah terdeteksi)"
                )

        frame_idx += 1

    cap.release()

    if log_fn:
        log_fn(
            f"[TRACK] Selesai: {analyzed_count} frame dianalisis, "
            f"{faces_found_count} frame dengan wajah terdeteksi."
        )

    return face_data


def generate_crop_data(
    face_data: List[dict],
    video_width: int,
    video_height: int,
    target_ratio: float = 9.0 / 16.0,
    smoothing: float = 0.12,
    log_fn: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """
    Convert raw face detection data into smoothed crop coordinates.

    Uses Exponential Moving Average (EMA) for smooth camera-like tracking.

    Args:
        face_data: Output from analyze_faces().
        video_width: Source video width in pixels.
        video_height: Source video height in pixels.
        target_ratio: Target width/height ratio (9/16 = 0.5625 for vertical).
        smoothing: EMA alpha (lower = smoother/slower, higher = more responsive).
        log_fn: Optional callback for logging.

    Returns:
        List of dicts: [
            {
                "timestamp": float,
                "crop_x": int,      # pixel X offset for crop
                "crop_w": int,      # crop width in pixels
                "crop_h": int,      # crop height in pixels (= video_height)
            }, ...
        ]
    """
    if not face_data:
        return []

    # Calculate crop dimensions
    crop_h = video_height
    crop_w = int(crop_h * target_ratio)

    # Ensure dimensions are even (required by H.264 / social media platforms)
    crop_w = crop_w & ~1  # Round down to nearest even number
    crop_h = crop_h & ~1

    # Ensure crop_w doesn't exceed video_width
    if crop_w > video_width:
        crop_w = video_width & ~1

    max_crop_x = video_width - crop_w
    center_crop_x = max_crop_x / 2.0  # default: center

    # Fill in None values (frames without face) using interpolation
    filled_data = _interpolate_missing(face_data, center_x_default=0.5)

    # Apply EMA smoothing
    smoothed_cx = None
    crop_coords = []

    for entry in filled_data:
        raw_cx = entry["center_x"]

        if smoothed_cx is None:
            smoothed_cx = raw_cx
        else:
            smoothed_cx = smoothing * raw_cx + (1.0 - smoothing) * smoothed_cx

        # Convert normalized center_x to pixel crop_x
        # The face center should be at the center of the crop window
        desired_center_px = smoothed_cx * video_width
        crop_x = desired_center_px - crop_w / 2.0

        # Clamp to valid range
        crop_x = max(0, min(max_crop_x, int(crop_x)))

        crop_coords.append(
            {
                "timestamp": entry["timestamp"],
                "crop_x": crop_x,
                "crop_w": crop_w,
                "crop_h": crop_h,
            }
        )

    if log_fn:
        log_fn(
            f"[TRACK] Crop data: {len(crop_coords)} keyframe, "
            f"crop size: {crop_w}x{crop_h}, smoothing: {smoothing}"
        )

    return crop_coords


def _interpolate_missing(
    face_data: List[dict], center_x_default: float = 0.5
) -> List[dict]:
    """
    Fill in None center_x/center_y values by linear interpolation
    between known face positions. Leading/trailing Nones use nearest value.
    """
    result = [dict(d) for d in face_data]  # shallow copy
    n = len(result)

    # Find indices with valid face data
    valid_indices = [i for i, d in enumerate(result) if d["center_x"] is not None]

    if not valid_indices:
        # No faces at all — use center
        for d in result:
            d["center_x"] = center_x_default
            d["center_y"] = 0.5
        return result

    # Fill leading Nones
    first_valid = valid_indices[0]
    for i in range(first_valid):
        result[i]["center_x"] = result[first_valid]["center_x"]
        result[i]["center_y"] = result[first_valid]["center_y"]

    # Fill trailing Nones
    last_valid = valid_indices[-1]
    for i in range(last_valid + 1, n):
        result[i]["center_x"] = result[last_valid]["center_x"]
        result[i]["center_y"] = result[last_valid]["center_y"]

    # Interpolate middle gaps
    for idx in range(len(valid_indices) - 1):
        start_i = valid_indices[idx]
        end_i = valid_indices[idx + 1]
        gap = end_i - start_i

        if gap <= 1:
            continue

        sx = result[start_i]["center_x"]
        sy = result[start_i]["center_y"]
        ex = result[end_i]["center_x"]
        ey = result[end_i]["center_y"]

        for j in range(start_i + 1, end_i):
            t = (j - start_i) / gap
            result[j]["center_x"] = sx + t * (ex - sx)
            result[j]["center_y"] = sy + t * (ey - sy)

    return result


def write_sendcmd_script(
    crop_data: List[dict], output_path: str
) -> str:
    """
    Write an FFmpeg sendcmd script file that dynamically changes
    the crop filter's X position over time.

    The script uses FFmpeg's sendcmd format:
        timestamp crop x int_value;

    Args:
        crop_data: Output from generate_crop_data().
        output_path: Path to write the sendcmd script.

    Returns:
        The output_path.
    """
    lines = []
    for entry in crop_data:
        ts = entry["timestamp"]
        cx = entry["crop_x"]
        lines.append(f"{ts:.3f} [enter] crop x {cx};")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path


def get_video_dimensions(video_path: str) -> Tuple[int, int, float]:
    """
    Get video width, height, and FPS using OpenCV.

    Returns:
        (width, height, fps) tuple. Returns (0, 0, 0) on error.
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return (0, 0, 0.0)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    return (w, h, fps)


def build_crop_filter_string(
    crop_data: List[dict],
    video_width: int,
    video_height: int,
) -> str:
    """
    Build an FFmpeg filtergraph string that uses the crop filter with
    frame-by-frame X position changes via timeline expressions.

    Instead of sendcmd (which is complex), this approach creates a crop
    filter with a mathematical expression that approximates the smooth
    tracking path using linear interpolation between keyframes.

    The expression uses FFmpeg's `t` (time in seconds) variable.

    Args:
        crop_data: Output from generate_crop_data().
        video_width: Source video width.
        video_height: Source video height.

    Returns:
        FFmpeg crop filter string, e.g.:
        "crop=w:h:x_expr:0"
    """
    if not crop_data:
        # Fallback: center crop (ensure even dimensions for H.264)
        crop_w = int(video_height * 9.0 / 16.0) & ~1
        crop_h = video_height & ~1
        crop_x = (video_width - crop_w) // 2
        return f"crop={crop_w}:{crop_h}:{crop_x}:0"

    crop_w = crop_data[0]["crop_w"]
    crop_h = crop_data[0]["crop_h"]

    # Reduce keyframes to manageable set (max ~40 keyframes for expression length to prevent FFmpeg -22 Invalid argument error)
    reduced = _reduce_keyframes(crop_data, max_keyframes=40)

    if len(reduced) <= 1:
        crop_x = reduced[0]["crop_x"] if reduced else (video_width - crop_w) // 2
        return f"crop={crop_w}:{crop_h}:{crop_x}:0"

    # Build piecewise linear expression using nested if/then/else
    # FFmpeg expression: if(lt(t,t1), x0 + (x1-x0)*(t-t0)/(t1-t0), if(lt(t,t2), ...))
    x_expr = _build_lerp_expression(reduced)

    return f"crop={crop_w}:{crop_h}:{x_expr}:0"


def _reduce_keyframes(
    crop_data: List[dict], max_keyframes: int = 40
) -> List[dict]:
    """Reduce number of keyframes by sampling evenly."""
    n = len(crop_data)
    if n <= max_keyframes:
        return crop_data

    step = n / max_keyframes
    result = []
    for i in range(max_keyframes):
        idx = min(int(i * step), n - 1)
        result.append(crop_data[idx])

    # Always include the last keyframe
    if result[-1] != crop_data[-1]:
        result.append(crop_data[-1])

    return result


def _build_lerp_expression(keyframes: List[dict]) -> str:
    """
    Build a piecewise-linear FFmpeg expression for crop X position.
    Uses nested if(lt(t,...), lerp, ...) expressions.
    """
    n = len(keyframes)
    if n == 0:
        return "0"
    if n == 1:
        return str(keyframes[0]["crop_x"])

    # Build from the last segment backwards (nested if/else)
    # Last segment: constant value (hold last position)
    expr = str(keyframes[-1]["crop_x"])

    for i in range(n - 2, -1, -1):
        t0 = keyframes[i]["timestamp"]
        t1 = keyframes[i + 1]["timestamp"]
        x0 = keyframes[i]["crop_x"]
        x1 = keyframes[i + 1]["crop_x"]

        dt = t1 - t0
        if dt < 0.001:
            # Degenerate segment — skip
            continue

        dx = x1 - x0

        if abs(dx) < 1:
            # No movement — use constant
            segment = str(x0)
        else:
            # Linear interpolation: x0 + (x1-x0) * (t-t0) / (t1-t0)
            segment = f"{x0}+{dx}*(t-{t0:.3f})/{dt:.3f}"

        expr = f"if(lt(t,{t1:.3f}),{segment},{expr})"

    return expr.replace(",", "\\,")
