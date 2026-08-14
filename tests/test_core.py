"""
tests/test_core.py — Smoke tests for the refactored Clipper backend.
"""

import os
import sys
import uuid
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models
import task_queue
import clipper
import virality
import thumbnail
import secure_store
import billing
import cloud_storage
import saas


def test_create_and_get_task():
    task_id = "test-task-create"
    models.create_task(task_id, params={"url": "https://example.com/video", "start": "0", "end": "10"})
    task = models.get_task(task_id)
    assert task is not None
    assert task["status"] == "pending"
    assert task["progress"] == 0
    assert task["params"]["url"] == "https://example.com/video"
    models.delete_task(task_id)


def test_update_task_and_logs():
    task_id = "test-task-logs-2"
    models.delete_task(task_id)
    models.create_task(task_id, params={})
    models.update_task(task_id, status="downloading", progress=25)
    models.append_log(task_id, "downloading video")
    models.append_log(task_id, "done")
    task = models.get_task(task_id)
    assert task["status"] == "downloading"
    assert task["progress"] == 25
    assert len(task["logs"]) == 2
    models.delete_task(task_id)


def test_clipper_state_compat():
    task_id = clipper.create_task()
    assert task_id
    task = clipper.get_task(task_id)
    assert task["status"] == "pending"
    clipper._update_task(task_id, status="done", progress=100)
    clipper._append_log(task_id, "completed")
    task = clipper.get_task(task_id)
    assert task["status"] == "done"
    assert len(task["logs"]) == 1


def test_queue_status():
    q = task_queue.get_queue(max_workers=2)
    status = task_queue.queue_status()
    assert status["max_workers"] == 2
    assert status["queued"] >= 0
    assert status["running"] >= 0


def test_parse_seconds_helpers():
    assert clipper._parse_seconds("90") == 90.0
    assert clipper._parse_seconds("01:30") == 90.0
    assert clipper._parse_seconds("00:01:30") == 90.0


def test_ytdlp_format_builder():
    primary, fallback = clipper._build_ytdlp_formats("best")
    assert "bestvideo" in primary
    primary, fallback = clipper._build_ytdlp_formats("1080")
    assert "height<=1080" in primary
    assert "height<=1080" in fallback


def test_quality_profile():
    high = clipper._get_quality_profile("high")
    assert high["crf"] == "18"
    assert high["preset"] == "medium"
    standard = clipper._get_quality_profile("standard")
    assert standard["crf"] == "22"
    assert standard["preset"] == "fast"


def test_vertical_target_height():
    assert clipper._vertical_target_height("source", 1080) == 1080
    assert clipper._vertical_target_height("1080", 2160) == 1920
    assert clipper._vertical_target_height("1080", 1080) == 1080  # source height is 1080, no upscale
    assert clipper._vertical_target_height("1080", 720) == 720
    assert clipper._vertical_target_height("720", 2160) == 1280
    assert clipper._vertical_target_height("720", 720) == 720


def test_virality_score_range():
    result = virality.score_moment(0, 45, hook_title="Fakta Gila Terbongkar!", transcript_segments=[
        {"start": "00:00:00", "end": "00:00:05", "text": "Ini fakta gila yang tidak disangka."},
        {"start": "00:00:05", "end": "00:00:10", "text": "Apakah kamu siap?"},
    ])
    assert 0 <= result["score"] <= 100
    assert result["badge"] in ("high", "medium", "low")
    assert result["reason"]
    assert "breakdown" in result


def test_virality_score_ideal_clip():
    segments = [
        {"start": "00:00:00", "end": "00:00:15", "text": "Syok! Rahasia viral ini akhirnya terbongkar."},
        {"start": "00:00:15", "end": "00:00:30", "text": "Dia ketahuan bohong selama 10 tahun."},
        {"start": "00:00:30", "end": "00:00:45", "text": "Jangan skip kalau tidak mau kaget!"},
    ]
    result = virality.score_moment(0, 45, hook_title="RAHASIA VIRAL TERBONGKAR", transcript_segments=segments)
    assert result["score"] >= 50


def test_virality_score_poor_clip():
    segments = [
        {"start": "00:00:00", "end": "00:00:02", "text": "halo."},
    ]
    result = virality.score_moment(0, 5, hook_title="video", transcript_segments=segments)
    assert result["score"] < 50


def test_thumbnail_module_missing_video():
    files = thumbnail.generate_thumbnails(
        video_path="/nonexistent/path/video.mp4",
        hook_title="TEST HOOK",
        output_dir="outputs",
        task_id="test-thumb",
    )
    assert files == []


def test_extract_clip_segments():
    import tempfile
    srt_content = """1
00:00:01,000 --> 00:00:05,000
First line.

2
00:00:06,000 --> 00:00:10,000
Second line.

3
00:00:11,000 --> 00:00:15,000
Third line.
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write(srt_content)
        path = f.name
    try:
        segments = clipper._extract_clip_segments(path, 3, 9)
        assert len(segments) == 2
        assert segments[0]["text"] == "First line."
        assert segments[1]["text"] == "Second line."
    finally:
        os.remove(path)


# ── Auth tests ───────────────────────────────────────────────────────────────

import app as clipper_app


def _auth_client():
    clipper_app.app.config["TESTING"] = True
    return clipper_app.app.test_client()


def test_auth_register_and_login():
    client = _auth_client()
    email = f"auth_test_{os.getpid()}@example.com"

    # Register
    res = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Test User",
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["user"]["email"] == email
    # JWT cookies should be set as httpOnly
    cookies = res.headers.getlist("Set-Cookie")
    assert any("access_token" in c for c in cookies)
    assert any("refresh_token" in c for c in cookies)
    assert all("HttpOnly" in c for c in cookies)

    # Duplicate register
    res = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Test User",
    })
    assert res.status_code == 409

    # Login with wrong password
    res = client.post("/api/auth/login", json={
        "email": email,
        "password": "wrongpassword",
    })
    assert res.status_code == 401

    # Login with correct password
    res = client.post("/api/auth/login", json={
        "email": email,
        "password": "password123",
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["user"]["email"] == email

    # /api/auth/me should be authenticated
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    data = res.get_json()
    assert data["authenticated"] is True
    assert data["user"]["email"] == email

    # Refresh token endpoint should issue a new access token
    res = client.post("/api/auth/refresh")
    assert res.status_code == 200
    assert any("access_token" in c for c in res.headers.getlist("Set-Cookie"))

    # Logout
    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    # /api/auth/me should be unauthenticated after logout
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    data = res.get_json()
    assert data["authenticated"] is False
    assert data["user"] is None


def test_auth_register_validation():
    client = _auth_client()

    # Missing email/password
    res = client.post("/api/auth/register", json={"email": "", "password": ""})
    assert res.status_code == 400

    # Invalid email
    res = client.post("/api/auth/register", json={
        "email": "not-an-email",
        "password": "password123",
    })
    assert res.status_code == 400

    # Short password
    res = client.post("/api/auth/register", json={
        "email": "shortpass@example.com",
        "password": "123",
    })
    assert res.status_code == 400


def test_auth_me_unauthenticated():
    client = _auth_client()
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    data = res.get_json()
    assert data["authenticated"] is False


def _registered_client(label):
    client = _auth_client()
    email = f"tenant_{label}_{uuid.uuid4().hex}@example.com"
    response = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": label,
    })
    assert response.status_code == 200
    return client, response.get_json()["user"]["id"]


def test_task_queries_strictly_isolate_users():
    user_a = models.create_user(f"isolation_a_{uuid.uuid4().hex}@example.com", "password123")
    user_b = models.create_user(f"isolation_b_{uuid.uuid4().hex}@example.com", "password123")
    task_a = f"tenant-task-{uuid.uuid4().hex}"
    orphan = f"orphan-task-{uuid.uuid4().hex}"
    try:
        models.create_task(task_a, user_id=user_a.id, params={})
        models.create_task(orphan, params={})
        assert models.get_task(task_a, user_id=user_a.id) is not None
        assert models.get_task(task_a, user_id=user_b.id) is None
        assert models.get_task(orphan, user_id=user_a.id) is None
        assert models.task_belongs_to_user(task_a, user_a.id)
        assert not models.task_belongs_to_user(task_a, user_b.id)
        assert not models.delete_task(task_a, user_id=user_b.id)
    finally:
        models.delete_task(task_a)
        models.delete_task(orphan)


def test_download_route_isolates_user_directories():
    client_a, user_a = _registered_client("download-a")
    client_b, user_b = _registered_client("download-b")
    task_id = f"download-task-{uuid.uuid4().hex}"
    filename = f"clip_{task_id}.mp4"
    output_dir = clipper_app.get_user_output_dir(user_a)
    path = os.path.join(output_dir, filename)
    try:
        models.create_task(task_id, user_id=user_a, params={})
        models.update_task(task_id, status="done", output_file=filename)
        with open(path, "wb") as output_file:
            output_file.write(b"tenant-a")

        assert client_a.get(f"/download/{filename}").status_code == 200
        assert client_b.get(f"/download/{filename}").status_code == 404
        assert clipper_app.get_user_output_dir(user_a) != clipper_app.get_user_output_dir(user_b)
    finally:
        models.delete_task(task_id)
        if os.path.isfile(path):
            os.remove(path)


def test_user_secrets_and_cookies_are_encrypted_and_isolated():
    user_a = models.create_user(f"secret_a_{uuid.uuid4().hex}@example.com", "password123")
    user_b = models.create_user(f"secret_b_{uuid.uuid4().hex}@example.com", "password123")
    secret = "AIza-test-secret-value"
    cookie_content = b"example.com\tTRUE\t/\tTRUE\t0\tsession\tprivate-cookie"

    models.set_user_secret(user_a.id, "gemini_api_key", secret)
    assert models.get_user_secret(user_a.id, "gemini_api_key") == secret
    assert models.get_user_secret(user_b.id, "gemini_api_key") == ""

    secure_store.save_user_cookies(user_a.id, cookie_content)
    encrypted_path = secure_store.user_cookies_path(user_a.id)
    with open(encrypted_path, "rb") as encrypted_file:
        encrypted = encrypted_file.read()
    assert cookie_content not in encrypted
    assert not secure_store.has_user_cookies(user_b.id)
    with secure_store.materialize_user_cookies(user_a.id) as temporary_path:
        with open(temporary_path, "rb") as temporary_file:
            assert temporary_file.read() == cookie_content
    assert not os.path.exists(temporary_path)


def test_oauth_identity_is_idempotent():
    email = f"oauth_{uuid.uuid4().hex}@example.com"
    subject = uuid.uuid4().hex
    first = models.get_or_create_oauth_user("google", subject, email, "OAuth User")
    second = models.get_or_create_oauth_user("google", subject, email, "Changed Name")
    assert first.id == second.id
    assert first.email == email
    assert first.email_verified is True


def test_monthly_usage_quota_and_idempotency():
    user = models.create_user(f"usage_{uuid.uuid4().hex}@example.com", "password123")
    key = f"usage-test-{uuid.uuid4().hex}"
    saas.consume_usage(user.id, "clip_count", 1, key)
    saas.consume_usage(user.id, "clip_count", 1, key)
    summary = saas.usage_summary(user.id)
    assert summary["period"] == saas.period_key()
    assert summary["metrics"]["clip_count"]["used"] == 1
    with pytest.raises(PermissionError):
        saas.consume_usage(user.id, "clip_count", 99, f"{key}-overflow")


def test_admin_bypasses_usage_quota():
    user = models.create_user(f"admin_usage_{uuid.uuid4().hex}@example.com", "password123")
    models.set_user_admin(user.id, True)
    admin = models.get_user_by_id(user.id)
    assert admin.is_admin is True

    # Admin should be able to consume far beyond the free plan limit.
    key = f"admin-usage-test-{uuid.uuid4().hex}"
    saas.consume_usage(admin.id, "clip_count", 99, key)
    summary = saas.usage_summary(admin.id)
    assert summary["metrics"]["clip_count"]["used"] == 99


def test_subscription_plan_and_actions():
    user = models.create_user(f"subscription_{uuid.uuid4().hex}@example.com", "password123")
    subscription = saas.set_subscription(user.id, "pro", "active", "manual")
    assert subscription["plan_code"] == "pro"
    assert saas.usage_summary(user.id)["plan_code"] == "pro"
    paused = saas.change_subscription(user.id, "pause")
    assert paused["status"] == "paused"
    resumed = saas.change_subscription(user.id, "resume")
    assert resumed["status"] == "active"
    cancelled = saas.change_subscription(user.id, "cancel")
    assert cancelled["cancel_at_period_end"] is True


def test_local_asset_metadata_and_tenant_scope(tmp_path):
    user = models.create_user(f"asset_{uuid.uuid4().hex}@example.com", "password123")
    other = models.create_user(f"asset_other_{uuid.uuid4().hex}@example.com", "password123")
    task_id = f"asset-task-{uuid.uuid4().hex}"
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"video")
    try:
        models.create_task(task_id, user_id=user.id, params={})
        result = cloud_storage.persist_task_assets(task_id, str(output))
        assert result["clip"]["provider"] == "local"
        assert cloud_storage.get_asset(task_id, user.id, "clip") is not None
        assert cloud_storage.get_asset(task_id, other.id, "clip") is None
        assert cloud_storage.asset_urls(task_id, user.id)["download_url"].startswith("/download/")
    finally:
        models.delete_task(task_id)


def test_plans_usage_and_subscription_api():
    client, _ = _registered_client("saas-api")
    plans_response = client.get("/api/plans")
    assert plans_response.status_code == 200
    assert {plan["code"] for plan in plans_response.get_json()["plans"]} == {"free", "pro", "team", "agency"}
    usage_response = client.get("/api/usage")
    assert usage_response.status_code == 200
    assert "metrics" in usage_response.get_json()
    subscription_response = client.get("/api/subscription")
    assert subscription_response.status_code == 200
    assert subscription_response.get_json()["subscription"]["plan_code"] == "free"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
