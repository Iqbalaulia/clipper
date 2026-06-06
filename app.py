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
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/cookies-status")
def cookies_status():
    exists = os.path.isfile(COOKIES_FILE)
    return jsonify({"exists": exists})


@app.route("/upload-cookies", methods=["POST"])
def upload_cookies():
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file yang diunggah."}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nama file kosong."}), 400
    
    try:
        file.save(COOKIES_FILE)
        return jsonify({"success": True, "message": "File cookies.txt berhasil diunggah."})
    except Exception as e:
        return jsonify({"error": f"Gagal menyimpan file: {str(e)}"}), 500


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
    
    if not url or not start or not end:
        return jsonify({"error": "URL, Waktu Mulai, dan Waktu Selesai wajib diisi."}), 400
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
    
    hook_title        = (data.get("hook_title") or "").strip()
    hook_fontsize     = str(data.get("hook_fontsize") or "34").strip()
    hook_preset       = (data.get("hook_preset") or "yellow-pop").strip()
    
    cookies_file = COOKIES_FILE if os.path.isfile(COOKIES_FILE) else ""

    task_id = clipper.create_task()

    clipper.start_clip_thread(
        task_id=task_id,
        url=url,
        start=start,
        end=end,
        output_dir=OUTPUT_DIR,
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
        hook_title=hook_title,
        hook_fontsize=hook_fontsize,
        hook_preset=hook_preset,
        cookies=cookies_file,
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


@app.route("/generate-hook", methods=["POST"])
def generate_hook():
    """Meminta AI (Groq Llama 3) untuk membuat hook title singkat berdasarkan video."""
    data = request.get_json(force=True)
    url = data.get("url")
    api_key = data.get("api_key")
    start_time = data.get("start", "")
    end_time = data.get("end", "")

    if not url or not api_key:
        return jsonify({"error": "URL dan API Key wajib diisi"}), 400

    try:
        # 1. Ekstrak metadata video dengan yt-dlp
        cmd = [
            sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url
        ]
        if os.path.isfile(COOKIES_FILE):
            cmd += ["--cookies", COOKIES_FILE]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(r.stdout)
        
        title = info.get("title", "Video tanpa judul")
        description = info.get("description", "")
        # Batasi panjang deskripsi agar tidak membebani konteks AI
        if len(description) > 1000:
            description = description[:1000] + "..."

        time_context = ""
        if start_time and end_time:
            time_context = f"\nFokus pada klip yang diambil dari menit/detik ke-{start_time} hingga ke-{end_time}. Pastikan judul relevan dengan cuplikan spesifik ini!"

        # 2. Siapkan prompt untuk Llama 3
        prompt = f"""Kamu adalah ahli pembuat Hook Video TikTok/Reels/Shorts.
Tugas kamu adalah membuat 1 kalimat pendek (Maksimal 3-5 kata) yang SANGAT memancing rasa penasaran (clickbait positif) untuk judul video berikut:
Judul Asli: {title}
Deskripsi: {description}{time_context}

HANYA BERIKAN teks judulnya saja, tanpa tanda kutip, tanpa penjelasan, dan gunakan huruf kapital yang sesuai. Contoh: Tonton Sampai Habis!, Fakta Mengejutkan!, Rahasia Terbongkar!"""

        # 3. Panggil Groq API
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 50
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30
        )
        
        if not response.ok:
            return jsonify({"error": f"Groq API Error: {response.text}"}), response.status_code
            
        result = response.json()
        hook_title = result["choices"][0]["message"]["content"].strip()
        
        # Bersihkan dari tanda kutip jika AI bandel
        hook_title = hook_title.replace('"', '').replace("'", "")
        
        return jsonify({"hook_title": hook_title})

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Gagal mengekstrak info video: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/generate-copy", methods=["POST"])
def generate_copy():
    """Meminta AI (Groq Llama 3) untuk membuat copywriting berdasarkan deskripsi & waktu video."""
    data = request.get_json(force=True)
    url = data.get("url")
    api_key = data.get("api_key")
    start_time = data.get("start", "")
    end_time = data.get("end", "")

    if not url or not api_key:
        return jsonify({"error": "URL dan API Key wajib diisi"}), 400

    try:
        # 1. Ekstrak metadata video dengan yt-dlp
        cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url]
        use_cookies = bool(data.get("cookies", False))
        if use_cookies and os.path.isfile(COOKIES_FILE):
            cmd += ["--cookies", COOKIES_FILE]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(r.stdout)
        
        title = info.get("title", "Video tanpa judul")
        description = info.get("description", "")
        # Batasi panjang deskripsi agar tidak membebani konteks AI
        if len(description) > 1000:
            description = description[:1000] + "..."

        time_context = ""
        if start_time and end_time:
            time_context = f"\nFokus pada klip yang diambil dari menit/detik ke-{start_time} hingga ke-{end_time}. Pastikan copywriting kamu relevan dengan cuplikan spesifik ini!"

        # 2. Siapkan prompt untuk Llama 3
        prompt = f"""Kamu adalah Social Media Manager profesional. Buatkan draft copywriting viral untuk TikTok, Instagram Reels, dan YouTube Shorts berdasarkan video berikut:
Judul: {title}
Deskripsi: {description}{time_context}

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



# ── Detect Controversial Moments ────────────────────────────────────────────

@app.route("/detect-moments", methods=["POST"])
def detect_moments():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    num_moments = int(data.get("num_moments") or 4)
    subtitle_lang = (data.get("subtitle_lang") or "id,en").strip()
    use_cookies = os.path.isfile(COOKIES_FILE)

    if not url: return jsonify({"error": "URL video wajib diisi."}), 400
    if not api_key: return jsonify({"error": "Groq API Key wajib diisi."}), 400

    try:
        cmd = [
            sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist",
            "--js-runtimes", "node:node.exe",
            "--remote-components", "ejs:github",
            "--no-check-certificates",
        ]
        if use_cookies:
            cmd += ["--cookies", COOKIES_FILE]
        cmd.append(url)

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            stderr_text = (r.stderr or "") + (r.stdout or "")
            if "cookies are no longer valid" in stderr_text:
                return jsonify({
                    "error": "Cookies YouTube Anda sudah kadaluarsa (expired). Silakan ekspor ulang file cookies.txt terbaru dari browser dan upload kembali."
                }), 400
            elif "Sign in to confirm" in stderr_text or "bot" in stderr_text.lower():
                cookie_hint = (
                    " (cookies.txt sudah diupload tapi mungkin invalid/expired)"
                    if use_cookies
                    else " — Upload file cookies.txt terlebih dahulu di bagian 'Bypass Blokir'"
                )
                return jsonify({
                    "error": f"YouTube memblokir akses karena mendeteksi bot{cookie_hint}. "
                             "Gunakan ekstensi browser 'Get cookies.txt LOCALLY' untuk mengekspor cookies terbaru."
                }), 400
            return jsonify({"error": f"Gagal mengambil info video: {stderr_text[:400]}"}), 400

        info = json.loads(r.stdout)
        title = info.get("title", "Video tanpa judul")
        
        # Download subtitle
        transcript_text = ""
        sub_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", subtitle_lang, "--convert-subs", "srt",
            "--skip-download",
            "--js-runtimes", "node:node.exe",
            "--remote-components", "ejs:github",
            "--no-check-certificates",
            "--output", os.path.join(OUTPUT_DIR, "_scan_%(id)s.%(ext)s"),
            "--no-playlist",
        ]
        if use_cookies:
            sub_cmd += ["--cookies", COOKIES_FILE]
        sub_cmd.append(url)
        subprocess.run(sub_cmd, capture_output=True, text=True, timeout=60)
        
        video_id = info.get("id", "unknown")
        for f in os.listdir(OUTPUT_DIR):
            if f.startswith(f"_scan_{video_id}") and f.endswith(".srt"):
                try:
                    with open(os.path.join(OUTPUT_DIR, f), "r", encoding="utf-8") as srt_f:
                        transcript_text += srt_f.read()
                except:
                    pass

        prompt = f'''Kamu adalah AI Video Editor. Cari {num_moments} momen paling menarik/kontroversial dari video ini.
Judul: {title}
Subtitle/Transcript:
{transcript_text[:10000]}

PENTING: Hapus kata "Momen" dari judul, dan buat judul tersebut memiliki hooks maksimal (clickbait positif yang sangat memancing rasa penasaran, maksimal 3-5 kata). Output HARUS berupa JSON murni dengan format:
[
  {{"index": 1, "start": "00:01:00", "end": "00:01:30", "title": "Fakta Mengejutkan!", "description": "Deskripsi singkat"}}, ...
]'''
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        
        # Since we use json_object, let's adjust prompt to demand an object
        prompt += "\nBerikan dalam bentuk JSON object dengan key 'moments' berisi array tersebut."
        payload["messages"][0]["content"] = prompt
        
        r_ai = requests.post(groq_url, headers=headers, json=payload, timeout=60)
        r_ai.raise_for_status()
        rd = r_ai.json()
        content_ai = rd["choices"][0]["message"]["content"]
        moments = json.loads(content_ai).get("moments", [])
        
        return jsonify({
            "moments": moments,
            "title": title,
            "has_transcript": bool(transcript_text)
        })

    except Exception as e:
        err_str = str(e)
        if "Sign in to confirm" in err_str or "bot" in err_str.lower():
            return jsonify({
                "error": "YouTube memblokir akses (bot detection). Upload cookies.txt yang valid untuk melanjutkan."
            }), 400
        return jsonify({"error": err_str}), 500






@app.route("/clip-moments", methods=["POST"])
def clip_moments():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    moments = data.get("moments") or []
    cookies_file = COOKIES_FILE if os.path.isfile(COOKIES_FILE) else ""

    if not url or not moments:
        return jsonify({"error": "URL dan momen wajib diisi."}), 400

    # Subtitle settings
    subtitle_enabled  = bool(data.get("subtitle_enabled", False))
    subtitle_lang     = (data.get("subtitle_lang") or "id,en").strip()
    subtitle_type     = (data.get("subtitle_type") or "burn").strip()
    subtitle_auto     = bool(data.get("subtitle_auto", True))
    subtitle_position = (data.get("subtitle_position") or "bottom").strip()
    sub_fontsize      = str(data.get("sub_fontsize") or "20").strip()
    sub_case          = (data.get("sub_case") or "normal").strip()
    sub_bold          = bool(data.get("sub_bold", False))
    sub_italic        = bool(data.get("sub_italic", False))
    sub_underline     = bool(data.get("sub_underline", False))
    sub_primary_color = (data.get("sub_primary_color") or "FFFFFF").strip().lstrip("#")
    sub_outline_color = (data.get("sub_outline_color") or "000000").strip().lstrip("#")
    sub_back_color    = (data.get("sub_back_color") or "000000").strip().lstrip("#")
    sub_back_alpha    = (data.get("sub_back_alpha") or "80").strip()
    sub_border_style  = str(data.get("sub_border_style") or "1").strip()
    sub_outline_width = str(data.get("sub_outline_width") or "2").strip()
    sub_shadow        = str(data.get("sub_shadow") or "1").strip()

    task_list = []
    for moment in moments:
        start = str(moment.get("start", "00:00:00"))
        end   = str(moment.get("end",   "00:01:00"))
        title = str(moment.get("title", ""))
        
        task_id = clipper.create_task()
        clipper.start_clip_thread(
            task_id=task_id,
            url=url,
            start=start,
            end=end,
            output_dir=OUTPUT_DIR,
            video_format=data.get("video_format", "original"),
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
            sub_primary_color=sub_primary_color,
            sub_outline_color=sub_outline_color,
            sub_back_color=sub_back_color,
            sub_back_alpha=sub_back_alpha,
            sub_border_style=sub_border_style,
            sub_outline_width=sub_outline_width,
            sub_shadow=sub_shadow,
            hook_title=title,
            hook_fontsize=str(data.get("hook_fontsize", "34")),
            hook_preset=data.get("hook_preset", "yellow-pop"),
            hook_position=data.get("hook_position", "top"),
            cookies=cookies_file,
        )
        task_list.append({
            "task_id": task_id,
            "moment_index": moment.get("index", 0),
            "title": title,
            "start": start,
            "end": end
        })
    return jsonify({"tasks": task_list})


@app.route("/batch-progress", methods=["POST"])
def batch_progress():
    data = request.get_json(force=True)
    task_ids = data.get("task_ids") or []
    
    result = {}
    for tid in task_ids:
        t = clipper.get_task(tid)
        if t:
            result[tid] = {
                "status": t["status"],
                "progress": t["progress"],
                "file": t["output_file"],
                "error": t["error"],
            }
    return jsonify({"tasks": result})


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 52)
    print("  [*] Video Clipper -- http://localhost:5000")
    print("=" * 52)
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
