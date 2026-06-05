import os
import re

app_py_path = "app.py"

with open(app_py_path, "r", encoding="utf-8") as f:
    content = f.read()

# Add cookies endpoints if not present
cookies_code = """

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
"""

if "cookies-status" not in content:
    content = content.replace("def index():\n    return render_template(\"index.html\")\n", "def index():\n    return render_template(\"index.html\")\n" + cookies_code)

# Ensure COOKIES_FILE is declared
if "COOKIES_FILE =" not in content:
    content = content.replace("OUTPUT_DIR  = os.path.join(BASE_DIR, \"outputs\")\n", "OUTPUT_DIR  = os.path.join(BASE_DIR, \"outputs\")\nCOOKIES_FILE = os.path.join(BASE_DIR, \"cookies.txt\")\n")

# Replace cookies param in /clip
clip_old = """    hook_preset       = (data.get("hook_preset") or "yellow-pop").strip()

    task_id = clipper.create_task()"""
clip_new = """    hook_preset       = (data.get("hook_preset") or "yellow-pop").strip()
    
    use_cookies = bool(data.get("cookies", False))
    cookies_file = COOKIES_FILE if (use_cookies and os.path.isfile(COOKIES_FILE)) else ""

    task_id = clipper.create_task()"""
content = content.replace(clip_old, clip_new)

clip_call_old = """        hook_preset=hook_preset,
    )

    return jsonify({"task_id": task_id})"""
clip_call_new = """        hook_preset=hook_preset,
        cookies=cookies_file,
    )

    return jsonify({"task_id": task_id})"""
content = content.replace(clip_call_old, clip_call_new)

# Append /detect-moments, /clip-moments, /batch-progress if not present
if "def detect_moments():" not in content:
    missing_endpoints = """
# ── Detect Controversial Moments ────────────────────────────────────────────

@app.route("/detect-moments", methods=["POST"])
def detect_moments():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    api_key = (data.get("api_key") or "").strip()
    num_moments = int(data.get("num_moments") or 4)
    use_cookies = bool(data.get("cookies", False))

    if not url: return jsonify({"error": "URL video wajib diisi."}), 400
    if not api_key: return jsonify({"error": "Groq API Key wajib diisi."}), 400

    try:
        cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url]
        if use_cookies and os.path.isfile(COOKIES_FILE):
            cmd += ["--cookies", COOKIES_FILE]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return jsonify({"error": f"Gagal mengambil info video: {r.stderr[:300]}"}), 400

        info = json.loads(r.stdout)
        title = info.get("title", "Video tanpa judul")
        
        # Download subtitle
        transcript_text = ""
        sub_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", "id,en", "--convert-subs", "srt",
            "--skip-download",
            "--output", os.path.join(OUTPUT_DIR, "_scan_%(id)s.%(ext)s"),
            "--no-playlist", url
        ]
        if use_cookies and os.path.isfile(COOKIES_FILE):
            sub_cmd.insert(-1, "--cookies")
            sub_cmd.insert(-1, COOKIES_FILE)
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

PENTING: Output HARUS berupa JSON murni dengan format:
[
  {{"index": 1, "start": "00:01:00", "end": "00:01:30", "title": "Momen 1", "description": "Deskripsi singkat"}}, ...
]'''
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        
        # Since we use json_object, let's adjust prompt to demand an object
        prompt += "\\nBerikan dalam bentuk JSON object dengan key 'moments' berisi array tersebut."
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
        return jsonify({"error": str(e)}), 500


@app.route("/clip-moments", methods=["POST"])
def clip_moments():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    moments = data.get("moments") or []
    use_cookies = bool(data.get("cookies", False))
    cookies_file = COOKIES_FILE if (use_cookies and os.path.isfile(COOKIES_FILE)) else ""

    if not url or not moments:
        return jsonify({"error": "URL dan momen wajib diisi."}), 400

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
            subtitle_enabled=data.get("subtitle_enabled", False),
            hook_title=title,
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
"""
    # Insert before the Entry point
    content = content.replace("# ── Entry point ──────────────────────────────────────────────────────────────", missing_endpoints + "\n\n# ── Entry point ──────────────────────────────────────────────────────────────")

# Update generate-hook and generate-copy to use cookies
gen_hook_old = '''        cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)'''
gen_hook_new = '''        cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-playlist", url]
        use_cookies = bool(data.get("cookies", False))
        if use_cookies and os.path.isfile(COOKIES_FILE):
            cmd += ["--cookies", COOKIES_FILE]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)'''
content = content.replace(gen_hook_old, gen_hook_new)

with open(app_py_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied.")
