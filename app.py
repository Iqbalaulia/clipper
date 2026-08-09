"""
app.py — Flask server for Video Clipper
"""

import os
import json
import time
import sys
import uuid
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from urllib.parse import urljoin
import requests
import subprocess
import shutil
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    Response,
    stream_with_context,
    redirect,
    url_for,
)
from flask_jwt_extended import (
    JWTManager,
    jwt_required,
    verify_jwt_in_request,
    create_access_token,
    create_refresh_token,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
    get_jwt_identity,
)
import clipper
import models
import task_queue
import secure_store
import billing
import cloud_storage
import saas
import social_auth

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR  = os.path.join(BASE_DIR, "outputs")
LOGS_DIR    = os.path.join(BASE_DIR, "logs")


def get_user_output_dir(user_id: int) -> str:
    """Return the isolated output directory for one user."""
    path = os.path.join(OUTPUT_DIR, str(int(user_id)))
    os.makedirs(path, exist_ok=True)
    return path

# Pastikan runtime Node.js bawaan (node.exe di folder proyek) bisa ditemukan
# oleh subprocess yt-dlp meskipun aplikasi dijalankan dari cwd lain.
os.environ["PATH"] = BASE_DIR + os.pathsep + os.environ.get("PATH", "")

os.makedirs(LOGS_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "clipper.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("clipper")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())

# JWT setup: access + refresh tokens delivered as httpOnly cookies.
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", app.config["SECRET_KEY"])
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
app.config["JWT_ACCESS_COOKIE_NAME"] = "access_token"
app.config["JWT_REFRESH_COOKIE_NAME"] = "refresh_token"
app.config["JWT_COOKIE_SECURE"] = os.environ.get("JWT_COOKIE_SECURE", "false").lower() == "true"
app.config["JWT_COOKIE_SAMESITE"] = "Lax"
app.config["JWT_COOKIE_CSRF_PROTECT"] = False  # P3: enable CSRF double-submit cookie
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRES_HOURS", "1")))
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRES_DAYS", "30")))

jwt = JWTManager(app)

# Email configuration for verification & forgot-password emails.
app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST", "")
app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
app.config["SMTP_USER"] = os.environ.get("SMTP_USER", "")
app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD", "")
app.config["SMTP_FROM"] = os.environ.get("SMTP_FROM", app.config["SMTP_USER"])
app.config["SMTP_TLS"] = os.environ.get("SMTP_TLS", "true").lower() == "true"
app.config["PUBLIC_BASE_URL"] = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")
app.config["EMAIL_VERIFICATION_REQUIRED"] = os.environ.get("EMAIL_VERIFICATION_REQUIRED", "false").lower() == "true"
app.config["EMAIL_VERIFICATION_TOKEN_HOURS"] = int(os.environ.get("EMAIL_VERIFICATION_TOKEN_HOURS", "24"))
app.config["PASSWORD_RESET_TOKEN_HOURS"] = int(os.environ.get("PASSWORD_RESET_TOKEN_HOURS", "1"))


class _CurrentUser:
    """Flask-Login-like proxy backed by the JWT identity in the current request."""

    @property
    def _user(self):
        if not hasattr(request, "_jwt_user"):
            try:
                verify_jwt_in_request(optional=True)
                identity = get_jwt_identity()
                request._jwt_user = models.get_user_by_id(int(identity)) if identity else None
            except Exception:
                request._jwt_user = None
        return request._jwt_user

    @property
    def is_authenticated(self):
        return self._user is not None

    @property
    def id(self):
        return self._user.id if self._user else None

    @property
    def email(self):
        return self._user.email if self._user else None

    @property
    def name(self):
        return self._user.name if self._user else None


current_user = _CurrentUser()


def login_required(f):
    """Drop-in replacement for flask_login.login_required using JWT cookies."""
    return jwt_required()(f)


def _generate_token() -> str:
    """Generate a URL-safe random token."""
    return secrets.token_urlsafe(32)


def _token_expiry(hours: int) -> str:
    """Return an ISO timestamp `hours` from now."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _send_email(to: str, subject: str, body: str) -> bool:
    """Send an email using the configured SMTP server. Returns True on success."""
    host = app.config["SMTP_HOST"]
    if not host:
        logger.warning("SMTP_HOST not configured; email to %s was not sent.", to)
        return False

    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = app.config["SMTP_FROM"]
    msg["To"] = to

    try:
        if app.config["SMTP_TLS"]:
            server = smtplib.SMTP(host, app.config["SMTP_PORT"], timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP(host, app.config["SMTP_PORT"], timeout=10)
        user = app.config["SMTP_USER"]
        password = app.config["SMTP_PASSWORD"]
        if user and password:
            server.login(user, password)
        server.sendmail(app.config["SMTP_FROM"], [to], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        logger.exception("Failed to send email to %s: %s", to, e)
        return False


def _send_verification_email(user: models.User, token: str) -> bool:
    """Send an email verification link to the user."""
    verify_url = urljoin(app.config["PUBLIC_BASE_URL"], f"/verify-email?token={token}")
    subject = "Verifikasi Email - Clipper Studio"
    body = f"""
    <p>Halo {user.name or user.email},</p>
    <p>Terima kasih telah mendaftar di Clipper Studio. Klik link di bawah ini untuk memverifikasi email Anda:</p>
    <p><a href="{verify_url}">{verify_url}</a></p>
    <p>Link ini berlaku selama {app.config['EMAIL_VERIFICATION_TOKEN_HOURS']} jam.</p>
    <p>Jika Anda tidak mendaftar, abaikan email ini.</p>
    """
    return _send_email(user.email, subject, body)


def _send_password_reset_email(user: models.User, token: str) -> bool:
    """Send a password reset link to the user."""
    reset_url = urljoin(app.config["PUBLIC_BASE_URL"], f"/reset-password?token={token}")
    subject = "Reset Password - Clipper Studio"
    body = f"""
    <p>Halo {user.name or user.email},</p>
    <p>Kami menerima permintaan reset password untuk akun Anda. Klik link di bawah ini untuk mengatur password baru:</p>
    <p><a href="{reset_url}">{reset_url}</a></p>
    <p>Link ini berlaku selama {app.config['PASSWORD_RESET_TOKEN_HOURS']} jam.</p>
    <p>Jika Anda tidak meminta reset password, abaikan email ini.</p>
    """
    return _send_email(user.email, subject, body)


# Mark any tasks that were running when the server last stopped as errored
stale_count = models.reset_stale_tasks()
if stale_count:
    logger.info("Marked %d stale tasks as error after restart", stale_count)

# Start the task queue
MAX_CONCURRENT_WORKERS = int(os.environ.get("CLIPPER_MAX_WORKERS", "2"))
TASK_TIMEOUT = int(os.environ.get("CLIPPER_TASK_TIMEOUT", "3600"))
FREE_TASKS_PER_DAY = int(os.environ.get("FREE_TASKS_PER_DAY", "5"))
task_queue.get_queue(max_workers=MAX_CONCURRENT_WORKERS, task_timeout=TASK_TIMEOUT)


def _current_user_id():
    """Return the current authenticated user's id, or None."""
    return current_user.id if current_user.is_authenticated else None


def _resolve_api_key(data: dict) -> str:
    """Persist a supplied Gemini key encrypted, or use the user's stored key."""
    api_key = (data.get("api_key") or "").strip()
    if api_key:
        models.set_user_secret(_current_user_id(), "gemini_api_key", api_key)
        return api_key
    return models.get_user_secret(_current_user_id(), "gemini_api_key")


def _quota_error(requested_tasks: int = 1):
    summary = saas.usage_summary(_current_user_id())
    quota = summary["metrics"]["clip_count"]
    if quota["used"] + requested_tasks <= quota["limit"]:
        return None
    return jsonify({"error": "Kuota clip bulan ini telah habis.", "usage": summary}), 429


def _rate_limit(scope: str, limit: int, window_seconds: int = 60):
    if app.config.get("TESTING"):
        return None
    identity = _current_user_id() or request.remote_addr or "anonymous"
    allowed, retry_after = saas.check_rate_limit(f"{scope}:{identity}", limit, window_seconds)
    if allowed:
        return None
    response = jsonify({"error": "Terlalu banyak permintaan. Silakan coba lagi.", "retry_after": retry_after})
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after)
    return response


def _asset_payload(task):
    return cloud_storage.asset_urls(task["id"], task["user_id"]) if task else {}


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/verify-email")
def verify_email_page():
    """Landing page for email verification links."""
    return render_template("index.html")


@app.route("/reset-password")
def reset_password_page():
    """Landing page for password reset links."""
    return render_template("index.html")


# ── Auth routes ───────────────────────────────────────────────────────────────

def _set_auth_cookies(response, user_id: int):
    """Attach JWT access and refresh tokens as httpOnly cookies."""
    identity = str(user_id)
    access_token = create_access_token(identity=identity)
    refresh_token = create_refresh_token(identity=identity)
    set_access_cookies(response, access_token)
    set_refresh_cookies(response, refresh_token)




@app.route("/api/auth/register", methods=["POST"])
def register():
    limited = _rate_limit("register", 5, 3600)
    if limited:
        return limited
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    name = (data.get("name") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email dan password wajib diisi."}), 400

    try:
        user = models.create_user(email, password, name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not user:
        return jsonify({"error": "Email sudah terdaftar."}), 409

    # Send verification email if SMTP is configured.
    verification_token = _generate_token()
    expiry = _token_expiry(app.config["EMAIL_VERIFICATION_TOKEN_HOURS"])
    models.set_email_verification_token(user.id, verification_token, expiry)
    email_sent = _send_verification_email(user, verification_token)

    response = jsonify({
        "success": True,
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "email_verification_required": app.config["EMAIL_VERIFICATION_REQUIRED"],
        "email_verification_sent": email_sent,
    })
    _set_auth_cookies(response, user.id)
    return response


@app.route("/api/auth/login", methods=["POST"])
def login():
    limited = _rate_limit("login", 10, 300)
    if limited:
        return limited
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email dan password wajib diisi."}), 400

    user = models.authenticate_user(email, password)
    if not user:
        return jsonify({"error": "Email atau password salah."}), 401
    if not user.is_active:
        return jsonify({"error": "Akun tidak aktif."}), 403
    if app.config["EMAIL_VERIFICATION_REQUIRED"] and not user.email_verified:
        return jsonify({
            "error": "Email belum diverifikasi. Silakan cek inbox Anda.",
            "email_verification_required": True,
        }), 403

    response = jsonify({
        "success": True,
        "user": {"id": user.id, "email": user.email, "name": user.name},
    })
    _set_auth_cookies(response, user.id)
    return response


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def logout():
    response = jsonify({"success": True, "message": "Logout berhasil."})
    unset_jwt_cookies(response)
    return response


@app.route("/api/auth/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    access_token = create_access_token(identity=identity)
    response = jsonify({"success": True})
    set_access_cookies(response, access_token)
    return response


@app.route("/api/auth/me")
def me():
    if current_user.is_authenticated:
        user = current_user._user
        return jsonify({
            "authenticated": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "email_verified": user.email_verified,
                "avatar_url": user.avatar_url,
                "timezone": user.timezone,
                "language": user.language,
            },
            "email_verification_required": app.config["EMAIL_VERIFICATION_REQUIRED"],
            "usage": saas.usage_summary(user.id),
            "subscription": saas.get_subscription(user.id),
            "social_providers": social_auth.configured_providers(),
        })
    return jsonify({"authenticated": False, "user": None})


@app.route("/api/auth/social/providers")
def social_providers():
    return jsonify({"providers": social_auth.configured_providers()})


@app.route("/api/auth/social/<provider>")
def social_login(provider):
    try:
        callback = url_for("social_callback", provider=provider, _external=True)
        return redirect(social_auth.authorization_url(provider, callback, app.config["SECRET_KEY"]))
    except ValueError as exc:
        return redirect("/?auth_error=" + requests.utils.quote(str(exc)))


@app.route("/api/auth/social/<provider>/callback", methods=["GET", "POST"])
def social_callback(provider):
    try:
        code = request.values.get("code", "")
        state = request.values.get("state", "")
        callback = url_for("social_callback", provider=provider, _external=True)
        identity = social_auth.exchange_identity(provider, code, state, callback, app.config["SECRET_KEY"])
        if not identity.get("email_verified"):
            raise ValueError("Email social login belum terverifikasi.")
        user = models.get_or_create_oauth_user(
            provider, identity["subject"], identity["email"], identity.get("name", ""), identity.get("avatar_url", "")
        )
        if not user.is_active:
            raise ValueError("Akun tidak aktif.")
        response = redirect("/?auth_status=success")
        _set_auth_cookies(response, user.id)
        return response
    except Exception as exc:
        logger.warning("Social login %s failed: %s", provider, exc)
        return redirect("/?auth_error=" + requests.utils.quote(str(exc)))


@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    limited = _rate_limit("forgot-password", 5, 3600)
    if limited:
        return limited
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "Email wajib diisi."}), 400

    user = models.get_user_by_email(email)
    if user:
        token = _generate_token()
        expiry = _token_expiry(app.config["PASSWORD_RESET_TOKEN_HOURS"])
        models.set_password_reset_token(user.id, token, expiry)
        _send_password_reset_email(user, token)

    # Always return success to prevent email enumeration.
    return jsonify({
        "success": True,
        "message": "Jika email terdaftar, link reset password telah dikirim.",
    })


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json(force=True) or {}
    token = (data.get("token") or "").strip()
    new_password = data.get("password") or ""

    if not token or not new_password:
        return jsonify({"error": "Token dan password baru wajib diisi."}), 400

    try:
        ok = models.reset_password(token, new_password)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not ok:
        return jsonify({"error": "Token tidak valid atau sudah kadaluarsa."}), 400

    return jsonify({"success": True, "message": "Password berhasil diubah. Silakan masuk."})


@app.route("/api/auth/verify-email", methods=["POST"])
def verify_email():
    data = request.get_json(force=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "Token verifikasi wajib diisi."}), 400

    if models.verify_email(token):
        return jsonify({"success": True, "message": "Email berhasil diverifikasi."})
    return jsonify({"error": "Token tidak valid atau sudah kadaluarsa."}), 400


@app.route("/api/auth/resend-verification", methods=["POST"])
def resend_verification():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "Email wajib diisi."}), 400

    user = models.get_user_by_email(email)
    if user and not user.email_verified:
        token = _generate_token()
        expiry = _token_expiry(app.config["EMAIL_VERIFICATION_TOKEN_HOURS"])
        models.set_email_verification_token(user.id, token, expiry)
        _send_verification_email(user, token)

    return jsonify({
        "success": True,
        "message": "Jika email terdaftar dan belum terverifikasi, email verifikasi telah dikirim ulang.",
    })


@app.route("/api/auth/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "GET":
        user = current_user._user
        return jsonify({
            "success": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "avatar_url": user.avatar_url,
                "timezone": user.timezone,
                "language": user.language,
            },
        })

    data = request.get_json(force=True) or {}
    user = models.update_user_profile(
        current_user.id,
        name=data.get("name"),
        avatar_url=data.get("avatar_url"),
        timezone=data.get("timezone"),
        language=data.get("language"),
    )
    if not user:
        return jsonify({"error": "Gagal memperbarui profil."}), 400
    return jsonify({
        "success": True,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "timezone": user.timezone,
            "language": user.language,
        },
    })


@app.route("/cookies-status")
@login_required
def cookies_status():
    return jsonify({"exists": secure_store.has_user_cookies(_current_user_id())})


@app.route("/upload-cookies", methods=["POST"])
@login_required
def upload_cookies():
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file yang diunggah."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nama file kosong."}), 400

    try:
        secure_store.save_user_cookies(_current_user_id(), file.read())
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
            [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--version"],
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


@app.route("/video-info", methods=["POST"])
@login_required
def video_info():
    """Fetch video metadata (thumbnail, title, duration, channel) without downloading."""
    data = request.get_json(force=True)
    limited = _rate_limit("video-info", 20, 3600)
    if limited:
        return limited
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL wajib diisi."}), 400

    try:
        cmd = [
            sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--dump-json", "--no-playlist",
            "--no-check-certificates",
        ]
        with secure_store.materialize_user_cookies(_current_user_id()) as cookies_file:
            if cookies_file:
                cmd += ["--cookies", cookies_file]
            cmd.append(url)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return jsonify({"error": "Gagal mengambil info video."}), 400

        info = json.loads(r.stdout)

        # Pick best thumbnail
        thumbnail = info.get("thumbnail", "")
        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            # Prefer high-res thumbnail
            for t in reversed(thumbnails):
                if t.get("url"):
                    thumbnail = t["url"]
                    break

        duration_secs = int(info.get("duration") or 0)
        duration_str = f"{duration_secs // 3600:02d}:{(duration_secs % 3600) // 60:02d}:{duration_secs % 60:02d}"

        return jsonify({
            "title": info.get("title", "Video Tanpa Judul"),
            "channel": info.get("uploader") or info.get("channel") or "Unknown",
            "duration": duration_secs,
            "duration_str": duration_str,
            "thumbnail": thumbnail,
            "view_count": info.get("view_count", 0),
            "like_count": info.get("like_count", 0),
            "upload_date": info.get("upload_date", ""),
        })

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout saat mengambil info video."}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/clip", methods=["POST"])
@login_required
def clip():
    """Start a clip task. Returns task_id immediately."""
    limited = _rate_limit("clip", 10, 3600)
    if limited:
        return limited
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
    
    subtitle_style    = (data.get("subtitle_style") or "standard").strip()
    bgm_type          = (data.get("bgm_type") or "none").strip()

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
    hook_position     = (data.get("hook_position") or "top").strip()
    
    auto_broll        = bool(data.get("auto_broll", False))
    transcription_source = (data.get("transcription_source") or "auto").strip()
    whisper_model     = (data.get("whisper_model") or "base").strip()
    
    # Resolution & quality options
    _valid_download_res = {"best", "2160", "1440", "1080", "720", "480"}
    _valid_output_res   = {"source", "1080", "720", "480"}
    _valid_quality      = {"high", "standard", "draft"}
    download_resolution = str(data.get("download_resolution") or "best").strip().lower()
    output_resolution   = str(data.get("output_resolution") or "1080").strip().lower()
    output_quality      = str(data.get("output_quality") or "standard").strip().lower()
    if download_resolution not in _valid_download_res:
        download_resolution = "best"
    if output_resolution not in _valid_output_res:
        output_resolution = "source"
    if output_quality not in _valid_quality:
        output_quality = "standard"
    
    quota_response = _quota_error()
    if quota_response:
        return quota_response
    user_id = _current_user_id()
    user_output_dir = get_user_output_dir(user_id)

    # Disk space check (rough estimate: 500 MB minimum)
    if not _has_enough_disk_space(user_output_dir, min_bytes=500 * 1024 * 1024):
        return jsonify({"error": "Ruang disk tidak mencukupi. Silakan kosongkan minimal 500 MB."}), 507

    task_id = str(uuid.uuid4())
    try:
        saas.consume_task_usage(user_id, task_id, start, end)
    except PermissionError as exc:
        return jsonify({"error": str(exc), "usage": saas.usage_summary(user_id)}), 429
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
        "cookies_user_id": user_id,
        "auto_broll": auto_broll,
        "transcription_source": transcription_source,
        "whisper_model": whisper_model,
        "download_resolution": download_resolution,
        "output_resolution": output_resolution,
        "output_quality": output_quality,
    }
    task_queue.submit_task(
        task_id=task_id, url=url, start=start, end=end,
        output_dir=user_output_dir, kwargs=kwargs,
        user_id=user_id,
    )

    return jsonify({"task_id": task_id})


@app.route("/cancel/<task_id>", methods=["POST"])
@login_required
def cancel_task(task_id: str):
    """Cancel a queued or running task owned by the current user."""
    task = models.get_task(task_id, user_id=_current_user_id())
    if task is None:
        return jsonify({"error": "Task tidak ditemukan atau bukan milik Anda."}), 404
    ok = task_queue.cancel_task(task_id)
    if not ok:
        return jsonify({"error": "Task tidak ditemukan atau sudah selesai."}), 400
    return jsonify({"success": True, "message": "Task dibatalkan."})


@app.route("/queue-status")
@login_required
def queue_status():
    """Return current task queue status."""
    return jsonify(task_queue.queue_status())


@app.route("/progress/<task_id>")
@login_required
def progress(task_id: str):
    """Server-Sent Events stream for real-time progress updates."""

    @stream_with_context
    def generate():
        sent_logs = 0
        while True:
            task = clipper.get_task(task_id, user_id=_current_user_id())
            if task is None:
                yield _sse({"error": "Task tidak ditemukan atau bukan milik Anda."})
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
            payload.update(_asset_payload(task))
            yield _sse(payload)

            if task["status"] in ("done", "error", "cancelled"):
                break

            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download/<path:filename>")
@login_required
def download(filename: str):
    """Serve the clipped file for download if owned by current user."""
    task = models.get_task_by_output_file(filename, user_id=_current_user_id())
    if task is None:
        return jsonify({"error": "File tidak ditemukan atau bukan milik Anda."}), 404
    asset = cloud_storage.get_asset_by_filename(filename, _current_user_id(), "clip")
    signed = cloud_storage.signed_url(asset, download=True)
    if signed:
        return redirect(signed)
    return send_from_directory(get_user_output_dir(_current_user_id()), filename, as_attachment=True)


@app.route("/download-thumb/<path:filename>")
@login_required
def download_thumb(filename: str):
    """Serve a generated thumbnail image for download if owned by current user."""
    task = models.get_task_by_thumbnail_file(filename, user_id=_current_user_id())
    if task is None:
        return jsonify({"error": "File tidak ditemukan atau bukan milik Anda."}), 404
    asset = cloud_storage.get_asset_by_filename(filename, _current_user_id(), "thumbnail")
    signed = cloud_storage.signed_url(asset)
    if signed:
        return redirect(signed)
    return send_from_directory(get_user_output_dir(_current_user_id()), filename, as_attachment=True)


@app.route("/task-meta/<task_id>")
@login_required
def task_meta(task_id: str):
    """Return extended metadata for a finished task (virality score + thumbnail)."""
    task = clipper.get_task(task_id, user_id=_current_user_id())
    if task is None:
        return jsonify({"error": "Task tidak ditemukan atau bukan milik Anda."}), 404
    payload = {
        "task_id": task["id"],
        "status": task["status"],
        "output_file": task["output_file"],
        "virality_score": task.get("virality_score"),
        "virality_reason": task.get("virality_reason"),
        "thumbnail_file": task.get("thumbnail_file"),
        "moment_index": task.get("moment_index", 0),
    }
    payload.update(_asset_payload(task))
    return jsonify(payload)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _has_enough_disk_space(path: str, min_bytes: int = 500 * 1024 * 1024) -> bool:
    """Check whether the disk containing `path` has at least `min_bytes` free."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free >= min_bytes
    except Exception:
        return True


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
@login_required
def generate_hook():
    """Meminta AI (Gemini) untuk membuat hook title singkat berdasarkan video."""
    data = request.get_json(force=True)
    limited = _rate_limit("generate-hook", 20, 3600)
    if limited:
        return limited
    url = data.get("url")
    api_key = _resolve_api_key(data)
    start_time = data.get("start", "")
    end_time = data.get("end", "")

    if not url or not api_key:
        return jsonify({"error": "URL dan API Key wajib diisi"}), 400
    try:
        saas.consume_usage(_current_user_id(), "ai_copy", 1, f"hook:{uuid.uuid4().hex}")
    except PermissionError as exc:
        return jsonify({"error": str(exc), "usage": saas.usage_summary(_current_user_id())}), 429

    try:
        # 1. Ekstrak metadata video dengan yt-dlp
        cmd = [
            sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--dump-json", "--no-playlist", url
        ]
        with secure_store.materialize_user_cookies(_current_user_id()) as cookies_file:
            if cookies_file:
                cmd += ["--cookies", cookies_file]
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
@login_required
def generate_copy():
    """Meminta AI (Gemini) untuk membuat copywriting berdasarkan deskripsi & waktu video."""
    data = request.get_json(force=True)
    limited = _rate_limit("generate-copy", 30, 3600)
    if limited:
        return limited
    url = data.get("url")
    api_key = _resolve_api_key(data)
    start_time = data.get("start", "")
    end_time = data.get("end", "")
    clip_title = data.get("clip_title", "")
    clip_context = data.get("clip_context", "")
    language = (data.get("language") or "id").strip().lower()
    if language not in ("id", "en"):
        language = "id"

    if not url or not api_key:
        return jsonify({"error": "URL dan API Key wajib diisi"}), 400
    try:
        saas.consume_usage(_current_user_id(), "ai_copy", 1, f"copy:{uuid.uuid4().hex}")
    except PermissionError as exc:
        return jsonify({"error": str(exc), "usage": saas.usage_summary(_current_user_id())}), 429

    try:
        if clip_title and clip_context:
            title = clip_title
            description = f"Konteks pembicaraan dalam klip ini (berdasarkan transkrip video asli): {clip_context}"
            time_context = f"\nFokus penuh pada konteks di atas. Buatkan copywriting yang SANGAT SPESIFIK untuk klip pendek ini!"
        else:
            # 1. Ekstrak metadata video dengan yt-dlp
            cmd = [sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--dump-json", "--no-playlist", url]
            use_cookies = bool(data.get("cookies", False))
            with secure_store.materialize_user_cookies(_current_user_id()) as cookies_file:
                if use_cookies and cookies_file:
                    cmd += ["--cookies", cookies_file]
                r = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = json.loads(r.stdout)
            
            title = info.get("title", "Video tanpa judul")
            description = info.get("description", "")
            if len(description) > 1000:
                description = description[:1000] + "..."
            
            time_context = ""
            if start_time and end_time:
                time_context = f"\nFokus pada klip yang diambil dari menit/detik ke-{start_time} hingga ke-{end_time}. Pastikan copywriting kamu relevan dengan cuplikan spesifik ini!"

        # 2. Siapkan prompt untuk Gemini (minta output JSON)
        if language == "en":
            prompt = f"""You are a professional Social Media Manager. Create a viral copywriting draft for TikTok, Instagram Reels, and YouTube Shorts based on the following video:
Title: {title}
Description: {description}{time_context}

Return ONLY a valid JSON object with this exact structure (no markdown, no explanation):
{{
  "title": "1 attention-grabbing title sentence",
  "caption": "2-3 short engaging paragraphs, casual and relevant",
  "cta": "Invite viewers to interact such as like, comment, or follow",
  "hashtags": "5-8 relevant hashtags as a single line"
}}"""
        else:
            prompt = f"""Kamu adalah Social Media Manager profesional. Buatkan draft copywriting viral untuk TikTok, Instagram Reels, dan YouTube Shorts berdasarkan video berikut:
Judul: {title}
Deskripsi: {description}{time_context}

Kembalikan HANYA objek JSON valid dengan struktur persis ini (tanpa markdown, tanpa penjelasan):
{{
  "title": "1 kalimat judul yang bikin penasaran",
  "caption": "caption 2-3 paragraf singkat yang engaging, santai, dan relevan",
  "cta": "Ajak penonton untuk interaksi seperti like, komen, atau follow",
  "hashtags": "5-8 hashtag relevan dalam satu baris"
}}"""

        # 3. Panggil Gemini API dengan response JSON
        messages = [{"role": "user", "content": prompt}]
        try:
            generated_text, _ = _call_gemini(api_key, messages, response_json=True)
            copy_data = json.loads(generated_text)
            return jsonify({
                "title": title,
                "language": language,
                "title_hook": copy_data.get("title", ""),
                "caption": copy_data.get("caption", ""),
                "cta": copy_data.get("cta", ""),
                "hashtags": copy_data.get("hashtags", ""),
            })
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
@login_required
def detect_moments():
    data = request.get_json(force=True)
    limited = _rate_limit("detect-moments", 10, 3600)
    if limited:
        return limited
    url = (data.get("url") or "").strip()
    api_key = _resolve_api_key(data)
    num_moments = int(data.get("num_moments") or 4)
    subtitle_lang = (data.get("subtitle_lang") or "id,en").strip()
    user_output_dir = get_user_output_dir(_current_user_id())
    use_cookies = secure_store.has_user_cookies(_current_user_id())

    if not url: return jsonify({"error": "URL video wajib diisi."}), 400
    if not api_key: return jsonify({"error": "Gemini API Key wajib diisi."}), 400
    try:
        saas.consume_usage(_current_user_id(), "ai_scan", 1, f"scan:{uuid.uuid4().hex}")
    except PermissionError as exc:
        return jsonify({"error": str(exc), "usage": saas.usage_summary(_current_user_id())}), 429

    try:
        # ── Step 1: Ambil metadata video ────────────────────────────────
        cmd = [
            sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "--dump-json", "--no-playlist",
            "--no-check-certificates",
        ]
        with secure_store.materialize_user_cookies(_current_user_id()) as cookies_file:
            if cookies_file:
                cmd += ["--cookies", cookies_file]
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
            sys.executable, "-m", "yt_dlp", "--js-runtimes", "node",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", subtitle_lang, "--convert-subs", "srt",
            "--skip-download",
            "--no-check-certificates",
            "--output", os.path.join(user_output_dir, "_scan_%(id)s.%(ext)s"),
            "--no-playlist",
        ]
        with secure_store.materialize_user_cookies(_current_user_id()) as cookies_file:
            if cookies_file:
                sub_cmd += ["--cookies", cookies_file]
            sub_cmd.append(url)
            subprocess.run(sub_cmd, capture_output=True, text=True, timeout=90)

        video_id = info.get("id", "unknown")
        srt_files_found = []
        for f in os.listdir(user_output_dir):
            if f.startswith(f"_scan_{video_id}") and f.endswith(".srt"):
                srt_files_found.append(f)
                try:
                    with open(os.path.join(user_output_dir, f), "r", encoding="utf-8") as srt_f:
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
8. START harus dimulai tepat pada kalimat pemantik (Hook) yang langsung menarik perhatian, hindari jeda kosong atau kata kerja transisi yang tidak penting di 3 detik pertama.
9. Jika momen berupa plot twist atau pertanyaan kontroversial, klip boleh diakhiri tepat setelah klimaks/pertanyaan menggantung tersebut untuk memicu rasa penasaran (looping video) atau perdebatan di kolom komentar.
10. Pilih momen dengan retorika yang padat, intonasi tinggi/bersemangat, atau ekspresi emosional yang kuat.

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
@login_required
def clip_moments():
    limited = _rate_limit("clip-moments", 5, 3600)
    if limited:
        return limited
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    moments = data.get("moments") or []

    if not url or not moments:
        return jsonify({"error": "URL dan momen wajib diisi."}), 400

    quota_response = _quota_error(len(moments))
    if quota_response:
        return quota_response
    user_id = _current_user_id()
    user_output_dir = get_user_output_dir(user_id)

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
    subtitle_style    = (data.get("subtitle_style") or "standard").strip()
    bgm_type          = (data.get("bgm_type") or "none").strip()
    sub_primary_color = (data.get("sub_primary_color") or "FFFFFF").strip().lstrip("#")
    sub_outline_color = (data.get("sub_outline_color") or "000000").strip().lstrip("#")
    sub_back_color    = (data.get("sub_back_color") or "000000").strip().lstrip("#")
    sub_back_alpha    = (data.get("sub_back_alpha") or "80").strip()
    sub_border_style  = str(data.get("sub_border_style") or "1").strip()
    sub_outline_width = str(data.get("sub_outline_width") or "2").strip()
    sub_shadow        = str(data.get("sub_shadow") or "1").strip()
    auto_broll        = bool(data.get("auto_broll", False))
    transcription_source = (data.get("transcription_source") or "auto").strip()
    whisper_model     = (data.get("whisper_model") or "base").strip()

    # Resolution & quality options
    _valid_download_res = {"best", "2160", "1440", "1080", "720", "480"}
    _valid_output_res   = {"source", "1080", "720", "480"}
    _valid_quality      = {"high", "standard", "draft"}
    download_resolution = str(data.get("download_resolution") or "best").strip().lower()
    output_resolution   = str(data.get("output_resolution") or "1080").strip().lower()
    output_quality      = str(data.get("output_quality") or "standard").strip().lower()
    if download_resolution not in _valid_download_res:
        download_resolution = "best"
    if output_resolution not in _valid_output_res:
        output_resolution = "source"
    if output_quality not in _valid_quality:
        output_quality = "standard"

    prepared_tasks = []
    for idx, moment in enumerate(moments, start=1):
        start = str(moment.get("start", "00:00:00"))
        end   = str(moment.get("end",   "00:01:00"))
        title = str(moment.get("title", ""))
        moment_index = int(moment.get("index", idx))
        task_id = str(uuid.uuid4())
        prepared_tasks.append({
            "task_id": task_id, "start": start, "end": end, "title": title,
            "moment_index": moment_index, "source_index": moment.get("index", 0),
        })
    try:
        saas.consume_batch_task_usage(user_id, prepared_tasks)
    except PermissionError as exc:
        return jsonify({"error": str(exc), "usage": saas.usage_summary(user_id)}), 429

    task_list = []
    for prepared in prepared_tasks:
        task_id = prepared["task_id"]
        start = prepared["start"]
        end = prepared["end"]
        title = prepared["title"]
        moment_index = prepared["moment_index"]
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
            "video_format": data.get("video_format", "original"),
            "bgm_type": bgm_type,
            "sub_primary_color": sub_primary_color,
            "sub_outline_color": sub_outline_color,
            "sub_back_color": sub_back_color,
            "sub_back_alpha": sub_back_alpha,
            "sub_border_style": sub_border_style,
            "sub_outline_width": sub_outline_width,
            "sub_shadow": sub_shadow,
            "hook_title": title,
            "hook_fontsize": str(data.get("hook_fontsize", "34")),
            "hook_preset": data.get("hook_preset", "yellow-pop"),
            "hook_position": data.get("hook_position", "top"),
            "cookies_user_id": user_id,
            "auto_broll": auto_broll,
            "transcription_source": transcription_source,
            "whisper_model": whisper_model,
            "download_resolution": download_resolution,
            "output_resolution": output_resolution,
            "output_quality": output_quality,
            "moment_index": moment_index,
        }
        task_queue.submit_task(task_id, url, start, end, user_output_dir, kwargs, user_id=user_id)
        task_list.append({
            "task_id": task_id,
            "moment_index": prepared["source_index"],
            "title": title,
            "start": start,
            "end": end
        })
    return jsonify({"tasks": task_list})


@app.route("/batch-progress", methods=["POST"])
@login_required
def batch_progress():
    data = request.get_json(force=True)
    task_ids = data.get("task_ids") or []
    
    result = {}
    for tid in task_ids:
        t = clipper.get_task(tid, user_id=_current_user_id())
        if t:
            result[tid] = {
                "status": t["status"],
                "progress": t["progress"],
                "file": t["output_file"],
                "error": t["error"],
                "virality_score": t.get("virality_score"),
                "virality_reason": t.get("virality_reason"),
                "thumbnail_file": t.get("thumbnail_file"),
                "moment_index": t.get("moment_index", 0),
            }
            result[tid].update(_asset_payload(t))
    return jsonify({"tasks": result})


@app.route("/api/plans")
def plans():
    return jsonify({"plans": saas.public_plans(), "billing_configured": billing.configured()})


@app.route("/api/usage")
@login_required
def usage():
    return jsonify(saas.usage_summary(_current_user_id()))


@app.route("/api/subscription")
@login_required
def subscription():
    return jsonify({
        "subscription": saas.get_subscription(_current_user_id()),
        "usage": saas.usage_summary(_current_user_id()),
        "invoices": billing.list_invoices(_current_user_id()),
    })


@app.route("/api/billing/checkout", methods=["POST"])
@login_required
def billing_checkout():
    limited = _rate_limit("billing-checkout", 5, 3600)
    if limited:
        return limited
    try:
        result = billing.create_checkout(current_user._user, (request.get_json(force=True) or {}).get("plan_code", ""))
        return jsonify(result)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/billing/subscription", methods=["POST"])
@login_required
def billing_subscription_action():
    try:
        action = (request.get_json(force=True) or {}).get("action", "")
        return jsonify({"subscription": saas.change_subscription(_current_user_id(), action)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/billing/webhook/midtrans", methods=["POST"])
def midtrans_webhook():
    try:
        return jsonify(billing.process_webhook(request.get_json(force=True) or {}))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("=" * 52)
    print("  [*] Video Clipper -- http://localhost:5000")
    print("=" * 52)
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
