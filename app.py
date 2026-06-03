"""
app.py — Flask server for Video Clipper
"""

import os
import json
import time
import sys
import requests
import subprocess
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    Response,
    stream_with_context,
)
import clipper

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.path.join(BASE_DIR, "outputs")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/check-deps")
def check_deps():
    """Check whether yt-dlp and ffmpeg are accessible."""
    import subprocess, sys

    results = {}

    # yt-dlp (installed as Python module)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        results["yt_dlp"] = r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        results["yt_dlp"] = None

    # ffmpeg
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10,
        )
        first_line = r.stdout.splitlines()[0] if r.stdout else ""
        results["ffmpeg"] = first_line if r.returncode == 0 else None
    except Exception:
        results["ffmpeg"] = None

    return jsonify(results)


@app.route("/clip", methods=["POST"])
def clip():
    """Start a clip task. Returns task_id immediately."""
    data = request.get_json(force=True)

    url   = (data.get("url")   or "").strip()
    start = (data.get("start") or "").strip()
    end   = (data.get("end") or "").strip()
    
    hook_enabled = bool(data.get("hook_enabled", False))
    hook_start   = (data.get("hook_start") or "").strip()
    hook_end     = (data.get("hook_end") or "").strip()

    if not url or not start or not end:
        return jsonify({"error": "URL, Waktu Mulai, dan Waktu Selesai wajib diisi."}), 400
        
    if hook_enabled and (not hook_start or not hook_end):
        return jsonify({"error": "Waktu Mulai dan Waktu Selesai Hook wajib diisi jika Hook diaktifkan."}), 400
    if not start:
        return jsonify({"error": "Waktu mulai tidak boleh kosong."}), 400
    if not end:
        return jsonify({"error": "Waktu selesai tidak boleh kosong."}), 400

    # Subtitle options
    subtitle_enabled  = bool(data.get("subtitle_enabled", False))
    subtitle_lang     = (data.get("subtitle_lang")     or "id,en").strip()
    subtitle_type     = (data.get("subtitle_type")     or "soft").strip()
    subtitle_auto     = bool(data.get("subtitle_auto", True))
    subtitle_position = (data.get("subtitle_position") or "bottom").strip()
    sub_fontsize      = str(data.get("sub_fontsize")   or "20").strip()
    sub_case          = (data.get("sub_case")          or "normal").strip()
    sub_bold          = bool(data.get("sub_bold", False))
    sub_italic        = bool(data.get("sub_italic", False))
    sub_underline     = bool(data.get("sub_underline", False))

    # Subtitle color/style preset params
    sub_primary_color = (data.get("sub_primary_color") or "FFFFFF").strip().lstrip("#")
    sub_outline_color = (data.get("sub_outline_color") or "000000").strip().lstrip("#")
    sub_back_color    = (data.get("sub_back_color")    or "000000").strip().lstrip("#")
    sub_back_alpha    = (data.get("sub_back_alpha")    or "80").strip()
    sub_border_style  = str(data.get("sub_border_style") or "1").strip()
    sub_outline_width = str(data.get("sub_outline_width") or "2").strip()
    sub_shadow        = str(data.get("sub_shadow")       or "1").strip()

    # Video format
    video_format      = (data.get("video_format") or "original").strip()

    task_id = clipper.create_task()
    clipper.start_clip_thread(
        task_id, url, start, end, OUTPUT_DIR,
        subtitle_enabled=subtitle_enabled,
        subtitle_lang=subtitle_lang,
        subtitle_type=subtitle_type,
        subtitle_auto=subtitle_auto,
        subtitle_position=subtitle_position,
        sub_fontsize=sub_fontsize,
        sub_case=sub_case,
        sub_bold=sub_bold,
        sub_italic=sub_italic,
        sub_underline=sub_underline,
        video_format=video_format,
        sub_primary_color=sub_primary_color,
        sub_outline_color=sub_outline_color,
        sub_back_color=sub_back_color,
        sub_back_alpha=sub_back_alpha,
        sub_border_style=sub_border_style,
        sub_outline_width=sub_outline_width,
        sub_shadow=sub_shadow,
        hook_enabled=hook_enabled,
        hook_start=hook_start,
        hook_end=hook_end,
    )

    return jsonify({"task_id": task_id})


@app.route("/progress/<task_id>")
def progress(task_id: str):
    """Server-Sent Events stream for real-time progress updates."""

    @stream_with_context
    def generate():
        sent_logs = 0
        while True:
            task = clipper.get_task(task_id)
            if task is None:
                yield _sse({"error": "Task tidak ditemukan."})
                break

            # Send only new log lines
            new_logs = task["logs"][sent_logs:]
            sent_logs += len(new_logs)

            payload = {
                "status":   task["status"],
                "progress": task["progress"],
                "logs":     new_logs,
                "file":     task["output_file"],
                "error":    task["error"],
            }
            yield _sse(payload)

            if task["status"] in ("done", "error"):
                break

            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download/<path:filename>")
def download(filename: str):
    """Serve the clipped file for download."""
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.route("/generate-copy", methods=["POST"])
def generate_copy():
    data = request.json
    url = data.get("url")
    api_key = data.get("api_key")

    if not url or not api_key:
        return jsonify({"error": "URL dan API Key wajib diisi."}), 400

    try:
        # 1. Ambil metadata dengan yt-dlp
        yt_cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-warnings", url]
        result = subprocess.run(yt_cmd, capture_output=True, text=True, check=True)
        info = json.loads(result.stdout)

        title = info.get("title", "Video")
        description = info.get("description", "")[:1000] # Batasi max 1000 karakter

        # 2. Siapkan prompt untuk Gemini
        prompt = f"""Kamu adalah Social Media Manager profesional. Buatkan draft copywriting viral untuk TikTok, Instagram Reels, dan YouTube Shorts berdasarkan video berikut:
Judul: {title}
Deskripsi: {description}

Format output yang diinginkan:
🌟 **JUDUL VIDEO (Bait/Hook):**
(Tulis 1 kalimat judul yang bikin penasaran)

📝 **CAPTION:**
(Tulis caption 2-3 paragraf singkat yang engaging, santai, dan relevan)

🔥 **CALL TO ACTION (CTA):**
(Ajak penonton untuk interaksi seperti like, komen, atau follow)

🏷️ **HASHTAGS:**
(5-8 hashtag relevan)"""

        # 3. Panggil Groq API (OpenAI Compatible)
        # Menggunakan Llama 3 8B yang sangat cepat dan gratis
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}]
        }
        
        try:
            r = requests.post(groq_url, headers=headers, json=payload, timeout=30)
            rd = r.json()
            if r.status_code == 200:
                generated_text = rd["choices"][0]["message"]["content"]
                return jsonify({"copy": generated_text, "title": title})
            else:
                err_msg = rd.get("error", {}).get("message", "Unknown error")
                return jsonify({"error": f"Groq API Error: {err_msg}"}), 400
        except Exception as e:
            return jsonify({"error": f"API Error: {str(e)}"}), 400

    except subprocess.CalledProcessError:
        return jsonify({"error": "Gagal mengambil informasi video. Pastikan URL valid."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 52)
    print("  [*] Video Clipper -- http://localhost:5000")
    print("=" * 52)
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
