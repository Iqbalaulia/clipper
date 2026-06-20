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


def _call_gemini(api_key: str, messages: list, response_json: bool = False,
                  max_retries: int = 3, base_delay: float = 2.0,
                  model_name: str | None = None) -> tuple[str, str]:
    """Helper to call Gemini API via native REST endpoint.

    Automatically retries on transient errors (429, 500, 502, 503, 504)
    with exponential backoff. If a model is not found (404), falls back
    to the next candidate in the fallback list.
    """
    system_instruction = ""
    contents = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "system":
            system_instruction = content
        else:
            gemini_role = "user" if role == "user" else "model"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}]
            })

    payload = {
        "contents": contents
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    config = {}
    if response_json:
        config["responseMimeType"] = "application/json"
    if config:
        payload["generationConfig"] = config

    # Model candidates in preference order. Different API keys / regions may
    # support different names, so we try the latest stable ones first and
    # fall back to aliases / legacy names on 404.
    env_model = (os.environ.get("GEMINI_MODEL") or "").strip()
    if model_name:
        candidate_models = [model_name]
    elif env_model:
        candidate_models = [env_model]
    else:
        candidate_models = [
            "gemini-2.5-flash",
            "gemini-1.5-flash",
            "gemini-flash-latest",
            "gemini-1.5-pro",
        ]

    api_version = os.environ.get("GEMINI_API_VERSION", "v1beta").strip()
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    last_error = None

    for model in candidate_models:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={api_key}"

        for attempt in range(max_retries + 1):
            try:
                r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=90)

                # 404 means model not found / not supported -> try next model immediately.
                if r.status_code == 404:
                    try:
                        err_json = r.json()
                        err_msg = err_json.get("error", {}).get("message", "")
                    except Exception:
                        err_msg = r.text
                    print(f"[Gemini] Model '{model}' not found ({r.status_code}): {err_msg}. Trying fallback...")
                    last_error = ValueError(f"Gemini API Error: {err_msg}")
                    break

                if r.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)  # 2s, 4s, 8s
                    print(f"[Gemini] {r.status_code} on attempt {attempt + 1}/{max_retries + 1}, "
                          f"retrying in {delay:.0f}s...")
                    time.sleep(delay)
                    continue

                r.raise_for_status()

            except requests.exceptions.HTTPError as err:
                try:
                    err_json = r.json()
                    err_msg = err_json.get("error", {}).get("message", str(err))
                except Exception:
                    err_msg = f"{r.text} ({err})"
                last_error = ValueError(f"Gemini API Error: {err_msg}")
                raise last_error
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    print(f"[Gemini] Timeout on attempt {attempt + 1}/{max_retries + 1}, "
                          f"retrying in {delay:.0f}s...")
                    time.sleep(delay)
                    continue
                raise ValueError("Gemini API Error: Request timed out after multiple retries.")

            # Success — parse response
            res_data = r.json()
            try:
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return text, model
            except (KeyError, IndexError):
                raise ValueError(f"Unexpected response structure from Gemini API: {res_data}")

    # All models exhausted
    raise last_error or ValueError("Gemini API Error: All model candidates failed.")


@app.route("/generate-hook", methods=["POST"])
def generate_hook():
    """Meminta AI (Gemini) untuk membuat hook title singkat berdasarkan video."""
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
        if len(description) > 800:
            description = description[:800] + "..."

        time_context = ""
        if start_time and end_time:
            time_context = f"\nSegmen klip: {start_time} → {end_time}. Hook HARUS relevan dengan momen spesifik di rentang waktu tersebut."

        # 2. Prompt yang lebih tajam dan viral-focused
        prompt = f"""Kamu adalah viral content strategist untuk TikTok, Reels, dan YouTube Shorts kelas dunia.

Judul Video Asli: {title}
Deskripsi: {description}{time_context}

TUGASMU:
Buat 3 kandidat hook title untuk video ini. Hook harus:
- MAKSIMAL 5 kata (lebih pendek = lebih kuat)
- Memicu FOMO atau rasa penasaran ekstrem
- Menggunakan kata-kata power seperti: SYOK, KETAHUAN, TERBONGKAR, HARUS TONTON, TIDAK DISANGKA, GILA, RAHASIA, HANCUR, VIRAL, dll
- HURUF KAPITAL SEMUA untuk impact maksimal
- Tidak generik — harus spesifik ke konten video ini

Referensi hook viral sukses:
- "KETAHUAN! DIA BOHONG SELAMA INI"
- "FAKTA GILA YANG DISEMBUNYIKAN!"  
- "INI YANG TERJADI SEBENARNYA!"
- "MEREKA PANIK HABIS INI!"
- "TIDAK AKAN PERCAYA KALAU TIDAK LIHAT!"

Setelah membuat 3 kandidat, pilih 1 yang PALING impactful.

BALAS HANYA dengan teks hook final saja (tanpa penjelasan, tanpa nomor, tanpa tanda kutip). Maksimal 6 kata."""

        # 3. Panggil Gemini API
        messages = [
            {
                "role": "system",
                "content": "Kamu adalah ahli viral content. Tugasmu HANYA mengeluarkan hook title singkat, tanpa penjelasan apapun."
            },
            {"role": "user", "content": prompt}
        ]
        
        hook_title, _ = _call_gemini(api_key, messages)
        
        # Bersihkan output AI dari artefak yang tidak diinginkan
        # Hapus tanda kutip, newline berlebih, nomor urut, dll
        hook_title = hook_title.replace('"', '').replace("'", "")
        hook_title = hook_title.replace('\n', ' ').replace('\r', '')
        # Hapus prefix "1." atau "Hook:" jika AI tidak patuh instruksi
        hook_title = re.sub(r'^(hook\s*[:.]?\s*|final\s*[:.]?\s*|\d+\.\s*)', '', hook_title, flags=re.IGNORECASE).strip()
        # Batasi panjang akhir — ambil hanya 6 kata pertama jika terlalu panjang
        words = hook_title.split()
        if len(words) > 6:
            hook_title = ' '.join(words[:6])
        
        return jsonify({"hook_title": hook_title.upper()})  # Force UPPERCASE untuk impact

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Gagal mengekstrak info video: {e.stderr}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/generate-copy", methods=["POST"])
def generate_copy():
    """Meminta AI (Gemini) untuk membuat copywriting berdasarkan deskripsi & waktu video."""
    data = request.get_json(force=True)
    url = data.get("url")
    api_key = data.get("api_key")
    start_time = data.get("start", "")
    end_time = data.get("end", "")
    clip_title = data.get("clip_title", "")
    clip_context = data.get("clip_context", "")

    if not url or not api_key:
        return jsonify({"error": "URL dan API Key wajib diisi"}), 400

    try:
        if clip_title and clip_context:
            title = clip_title
            description = f"Konteks pembicaraan dalam klip ini (berdasarkan transkrip video asli): {clip_context}"
            time_context = f"\nFokus penuh pada konteks di atas. Buatkan copywriting yang SANGAT SPESIFIK untuk klip pendek ini!"
        else:
            # 1. Ekstrak metadata video dengan yt-dlp
            cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url]
            use_cookies = bool(data.get("cookies", False))
            if use_cookies and os.path.isfile(COOKIES_FILE):
                cmd += ["--cookies", COOKIES_FILE]
            r = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = json.loads(r.stdout)
            
            title = info.get("title", "Video tanpa judul")
            description = info.get("description", "")
            if len(description) > 1000:
                description = description[:1000] + "..."
            
            time_context = ""
            if start_time and end_time:
                time_context = f"\nFokus pada klip yang diambil dari menit/detik ke-{start_time} hingga ke-{end_time}. Pastikan copywriting kamu relevan dengan cuplikan spesifik ini!"

        # 2. Siapkan prompt untuk Gemini
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

        # 3. Panggil Gemini API
        messages = [{"role": "user", "content": prompt}]
        try:
            generated_text, _ = _call_gemini(api_key, messages)
            return jsonify({"copy": generated_text, "title": title})
        except Exception as e:
            return jsonify({"error": f"Gemini API Error: {str(e)}"}), 400

    except subprocess.CalledProcessError:
        return jsonify({"error": "Gagal mengambil informasi video. Pastikan URL valid."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ── Helpers for SRT parsing ─────────────────────────────────────────────────

import re

def parse_srt_to_segments(srt_text):
    """
    Parse raw SRT content into list of {start, end, text} dicts.
    Strips HTML tags, sequence numbers, and deduplicates repeated lines.
    Returns a list sorted by start time.
    """
    # Remove HTML/XML tags (e.g. <c>, <00:00:05.000>)
    srt_clean = re.sub(r'<[^>]+>', '', srt_text)
    # Split into blocks by blank lines
    blocks = re.split(r'\n\s*\n', srt_clean.strip())
    segments = []
    seen_texts = set()
    
    time_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2})[,.]\d{3}\s*-->\s*(\d{2}:\d{2}:\d{2})[,.]\d{3}'
    )
    
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        # Find timestamp line
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
        
        start_ts = m.group(1)  # HH:MM:SS
        end_ts   = m.group(2)
        text     = ' '.join(text_lines).strip()
        
        # Skip empty or duplicate consecutive text
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        
        segments.append({"start": start_ts, "end": end_ts, "text": text})
    
    return segments


def format_transcript_for_ai(segments, max_chars=None):
    """
    Format segments into a clean readable transcript for AI consumption.
    Format: [HH:MM:SS] text

    Args:
        segments: list of dicts with 'start', 'end', 'text'
        max_chars: optional hard limit. None means use all available transcript.
                   Gemini 1.5 Flash has a very large context window, so truncating
                   at 12k characters was artificially hurting long-video analysis.
    """
    lines = []
    total = 0
    for seg in segments:
        line = f"[{seg['start']}] {seg['text']}"
        if max_chars is not None and total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return '\n'.join(lines)


def srt_timestamp_to_seconds(ts):
    """Convert HH:MM:SS to integer seconds."""
    parts = ts.split(':')
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except:
        return 0


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
    if not api_key: return jsonify({"error": "Gemini API Key wajib diisi."}), 400

    try:
        # ── Step 1: Ambil metadata video ────────────────────────────────
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
        duration_secs = int(info.get("duration") or 0)  # total durasi video dalam detik
        duration_str  = f"{duration_secs // 3600:02d}:{(duration_secs % 3600) // 60:02d}:{duration_secs % 60:02d}"

        # ── Step 2: Download subtitle / auto-caption ─────────────────────
        transcript_text = ""
        raw_srt = ""
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

        sub_result = subprocess.run(sub_cmd, capture_output=True, text=True, timeout=90)

        video_id = info.get("id", "unknown")
        srt_files_found = []
        for f in os.listdir(OUTPUT_DIR):
            if f.startswith(f"_scan_{video_id}") and f.endswith(".srt"):
                srt_files_found.append(f)
                try:
                    with open(os.path.join(OUTPUT_DIR, f), "r", encoding="utf-8") as srt_f:
                        raw_srt += srt_f.read() + "\n"
                except Exception:
                    pass

        has_transcript = bool(raw_srt.strip())

        if has_transcript:
            # Parse & clean SRT
            segments = parse_srt_to_segments(raw_srt)
            transcript_text = format_transcript_for_ai(segments)
        
        # ── Step 3: Susun prompt yang akurat ────────────────────────────
        if has_transcript and transcript_text:
            transcript_section = f"""TRANSCRIPT (dengan timestamp, format [HH:MM:SS] teks):
{transcript_text}"""
            ai_basis = "transcript nyata di atas"
        else:
            # Fallback: tidak ada transcript
            transcript_section = f"""PERHATIAN: Tidak ada transcript yang tersedia untuk video ini.
Gunakan pengetahuanmu tentang video berjudul \"{title}\" untuk memperkirakan momen yang menarik.
Pastikan timestamp yang kamu buat MASUK AKAL dan tidak melebihi durasi video ({duration_str}).
Durasi video: {duration_str} ({duration_secs} detik)."""
            ai_basis = "pengetahuan tentang topik video"

        prompt = f"""Kamu adalah AI Video Editor profesional yang bertugas menemukan momen paling menarik, kontroversial, atau viral dari sebuah video.

INFORMASI VIDEO:
- Judul: {title}
- Durasi total: {duration_str} ({duration_secs} detik)

{transcript_section}

TUGAS:
Temukan tepat {num_moments} momen terbaik yang:
1. Memiliki potensi viral tinggi (konflik, fakta mengejutkan, momen lucu, pernyataan kontroversial, plot twist, dll)
2. Berdurasi antara 30 detik hingga 90 detik per klip
3. Timestamp START dan END HARUS akurat berdasarkan {ai_basis}
4. Timestamp END TIDAK BOLEH melebihi {duration_str}
5. START dan END HARUS dalam format HH:MM:SS
6. Setiap klip HARUS berupa 1 konteks penuh: jangan potong di tengah kalimat. Pilih START di awal kalimat dan END di akhir kalimat yang masuk akal. Jika perlu, perpanjang sedikit agar kalimat terakhir selesai.
7. Jika momen berisi percakapan, pastikan klip berakhir setelah penutup/pernyataan penting, bukan di tengah jawaban.

RETURN JSON OBJECT dengan key 'moments' berisi array {num_moments} objek, masing-masing:
{{
  "index": (nomor urut 1-{num_moments}),
  "start": "HH:MM:SS",
  "end": "HH:MM:SS",
  "title": "(HOOK TITLE VIRAL: Maksimal 5 kata, HURUF KAPITAL SEMUA, memicu FOMO/penasaran ekstrem. Contoh: KETAHUAN! DIA BOHONG SELAMA INI, FAKTA GILA YANG DISEMBUNYIKAN!)",
  "reason": "(1 kalimat alasan kenapa momen ini viral/kontroversial, berdasarkan isi transcript)",
  "confidence": (angka 1-10 yang menunjukkan seberapa yakin kamu momen ini viral)
}}

Contoh few-shot yang benar:
Momen 1:
- start: "00:02:15"
- end: "00:02:52"
- title: "DIA NGOMONG INI?!"
- reason: "Pernyataan kontroversial pembicara utama membuat lawan bicara terdiam sejenak."
- confidence: 9

Momen 2:
- start: "00:05:40"
- end: "00:06:08"
- title: "FAKTA MENGEJUTKAN TERBONGKAR"
- reason: "Data yang disebutkan bertentangan dengan klaim sebelumnya dan menciptakan plot twist."
- confidence: 8

PENTING: Jika tidak yakin dengan timestamp akurat, preferensi berikan batas awal/akhir kalimat yang aman. Jangan tebak timestamp di tengah dialog."""

        # ── Step 4: Panggil Gemini AI ──────────────────────────────────────
        messages = [
            {
                "role": "system",
                "content": "Kamu adalah AI Video Editor ahli. Selalu kembalikan JSON yang valid dan PASTIKAN timestamp tidak melebihi durasi video."
            },
            {"role": "user", "content": prompt}
        ]
        
        try:
            content_ai, model_used = _call_gemini(api_key, messages, response_json=True)
            moments_raw = json.loads(content_ai).get("moments", [])
        except Exception as e:
            return jsonify({"error": f"Gemini API Error: {str(e)}"}), 500

        # ── Step 5: Validasi timestamp ───────────────────────────────────
        moments_valid = []
        for m in moments_raw:
            start_s = srt_timestamp_to_seconds(str(m.get("start", "00:00:00")))
            end_s   = srt_timestamp_to_seconds(str(m.get("end",   "00:01:00")))

            # Clamp end ke durasi video
            if duration_secs > 0:
                end_s = min(end_s, duration_secs)

            # Pastikan end > start dan durasi minimal 15 detik
            if end_s <= start_s:
                end_s = min(start_s + 60, duration_secs if duration_secs > 0 else start_s + 60)

            # Snap ke batas kalimat agar setiap clip berisi 1 konteks penuh.
            if has_transcript and segments:
                try:
                    snapped_start, snapped_end = clipper._snap_to_sentence_boundaries(
                        segments, start_s, end_s, duration_secs=duration_secs
                    )
                    start_s, end_s = snapped_start, snapped_end
                except Exception:
                    pass

            # Konversi balik ke HH:MM:SS
            def secs_to_ts(s):
                s = max(0, int(s))
                return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

            moments_valid.append({
                "index":  m.get("index", len(moments_valid) + 1),
                "start":  secs_to_ts(start_s),
                "end":    secs_to_ts(end_s),
                "title":  str(m.get("title", f"Momen {len(moments_valid)+1}")).upper(),
                "reason": str(m.get("reason", m.get("description", ""))),
                "confidence": int(m.get("confidence", 0)) if isinstance(m.get("confidence"), (int, float)) else 0,
            })

        return jsonify({
            "moments":        moments_valid,
            "video_title":    title,
            "has_transcript": has_transcript,
            "model_used":     model_used if model_used else "gemini-1.5-flash",
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
