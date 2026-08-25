"""
tests/test_core.py — Smoke tests for the refactored Clipper backend.
"""

import os
import sys
import uuid
import hashlib
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
import notifications
import library
import webhooks
import apikeys


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


# ── Admin dashboard tests ────────────────────────────────────────────────────


def test_admin_me_returns_is_admin():
    client = _auth_client()
    email = f"admin_me_{uuid.uuid4().hex}@example.com"
    res = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Admin Me",
    })
    assert res.status_code == 200
    user_id = res.get_json()["user"]["id"]
    models.set_user_admin(user_id, True)
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    data = res.get_json()
    assert data["authenticated"] is True
    assert data["user"]["is_admin"] is True


def test_admin_required_blocks_non_admin():
    client = _auth_client()
    email = f"non_admin_{uuid.uuid4().hex}@example.com"
    client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Non Admin",
    })
    res = client.get("/api/admin/stats")
    assert res.status_code == 403


def test_admin_list_users():
    client = _auth_client()
    email = f"admin_list_{uuid.uuid4().hex}@example.com"
    res = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Admin List",
    })
    user_id = res.get_json()["user"]["id"]
    models.set_user_admin(user_id, True)
    res = client.get("/api/admin/users")
    assert res.status_code == 200
    data = res.get_json()
    assert "users" in data
    assert "total" in data
    assert any(u["email"] == email for u in data["users"])


def test_admin_suspend_user_blocks_login():
    admin_client = _auth_client()
    admin_email = f"admin_suspend_{uuid.uuid4().hex}@example.com"
    res = admin_client.post("/api/auth/register", json={
        "email": admin_email,
        "password": "password123",
        "name": "Admin Suspend",
    })
    admin_id = res.get_json()["user"]["id"]
    models.set_user_admin(admin_id, True)

    user_client = _auth_client()
    email = f"suspend_{uuid.uuid4().hex}@example.com"
    res = user_client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Suspend Me",
    })
    user_id = res.get_json()["user"]["id"]

    # Suspend the first user using admin client
    res = admin_client.patch(f"/api/admin/users/{user_id}", json={"is_active": False})
    assert res.status_code == 200

    # Login as suspended user should fail
    res = user_client.post("/api/auth/login", json={
        "email": email,
        "password": "password123",
    })
    assert res.status_code == 403

    # /api/auth/me should also report not authenticated
    res = user_client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.get_json()["authenticated"] is False


def test_admin_stats_endpoint():
    client = _auth_client()
    email = f"admin_stats_{uuid.uuid4().hex}@example.com"
    res = client.post("/api/auth/register", json={
        "email": email,
        "password": "password123",
        "name": "Admin Stats",
    })
    user_id = res.get_json()["user"]["id"]
    models.set_user_admin(user_id, True)
    res = client.get("/api/admin/stats")
    assert res.status_code == 200
    data = res.get_json()
    assert "users" in data
    assert "tasks" in data
    assert "revenue" in data
    assert "queue" in data
    assert "storage" in data



# ── Notification tests ───────────────────────────────────────────────────────


def test_in_app_notification_crud():
    user = models.create_user(f"notif_{uuid.uuid4().hex}@example.com", "password123")
    nid = notifications.create_notification(user.id, "Test title", body="Test body", link="/?tab=manual")
    assert nid > 0

    unread = notifications.count_unread_notifications(user.id)
    assert unread == 1

    items = notifications.list_notifications(user.id)
    assert len(items) == 1
    assert items[0]["title"] == "Test title"
    assert items[0]["is_read"] is False

    notifications.mark_notification_read(nid, user.id)
    assert notifications.count_unread_notifications(user.id) == 0

    notifications.mark_all_notifications_read(user.id)
    assert notifications.count_unread_notifications(user.id) == 0


def test_notification_preferences_defaults_and_update():
    user = models.create_user(f"pref_{uuid.uuid4().hex}@example.com", "password123")
    prefs = models.get_notification_preferences(user.id)
    assert prefs["email_task_done"] is True
    assert prefs["email_quota_alert"] is True
    assert prefs["email_payment"] is True
    assert prefs["email_marketing"] is False
    assert prefs["in_app_enabled"] is True

    updated = models.update_notification_preferences(user.id, {"email_task_done": False, "email_marketing": True})
    assert updated["email_task_done"] is False
    assert updated["email_marketing"] is True
    assert updated["email_quota_alert"] is True


def test_quota_alert_idempotency():
    user = models.create_user(f"quota_alert_{uuid.uuid4().hex}@example.com", "password123")
    # Consume 5 clip_count to hit the free plan limit (100%).
    # consume_usage will automatically fire 80% and 100% alerts.
    for i in range(5):
        saas.consume_usage(user.id, "clip_count", 1, f"quota-alert-{uuid.uuid4().hex}-{i}")

    event_key_100 = f"{saas.period_key()}:clip_count:100"
    assert models.was_notification_sent(user.id, "email:quota", event_key_100)

    # Re-issuing the same alert should be suppressed.
    notifications.notify_quota_alert(user.id, "clip_count", 100)

    # Should still be exactly one 100% in-app notification.
    items = notifications.list_notifications(user.id)
    hundred_items = [n for n in items if "habis" in n["title"]]
    assert len(hundred_items) == 1


def test_task_completion_creates_in_app_notification():
    user = models.create_user(f"task_notif_{uuid.uuid4().hex}@example.com", "password123")
    task_id = f"task-notif-{uuid.uuid4().hex}"
    models.create_task(task_id, user_id=user.id, params={})
    models.update_task(task_id, status="done", progress=100, output_file=f"clip_{task_id}.mp4")

    notifications.notify_task_completed(user.id, task_id)
    items = notifications.list_notifications(user.id)
    assert any(n["title"] == "Clip selesai diproses" for n in items)


def test_payment_webhook_triggers_notification():
    user = models.create_user(f"pay_notif_{uuid.uuid4().hex}@example.com", "password123")
    invoice_id = f"INV-{uuid.uuid4().hex[:20].upper()}"
    with models._connect() as conn:
        conn.execute(
            """INSERT INTO invoices (id, user_id, plan_code, amount, currency, status, provider_reference, checkout_url, created_at)
               VALUES (?, ?, 'pro', 99000, 'IDR', 'pending', 'tok-test', 'http://checkout', ?)""",
            (invoice_id, user.id, models._now()),
        )
        conn.commit()

    # Simulate a failed payment webhook.
    billing.process_webhook({
        "order_id": invoice_id,
        "transaction_status": "deny",
        "status_code": "200",
        "gross_amount": "99000",
        "signature_key": hashlib.sha512(f"{invoice_id}20099000{os.environ.get('MIDTRANS_SERVER_KEY', '')}".encode()).hexdigest(),
    })

    items = notifications.list_notifications(user.id)
    assert any("Pembayaran gagal" in n["title"] for n in items)


def test_notification_api_endpoints():
    client = _auth_client()
    email = f"notif_api_{uuid.uuid4().hex}@example.com"
    res = client.post("/api/auth/register", json={"email": email, "password": "password123", "name": "Notif API"})
    assert res.status_code == 200
    data = res.get_json()
    assert "unread_notifications" in data

    # Create a notification directly (mark welcome notification read first).
    user_id = data["user"]["id"]
    notifications.mark_all_notifications_read(user_id)
    nid = notifications.create_notification(user_id, "API Test", body="API body", link="/?tab=manual")

    res = client.get("/api/notifications/unread-count")
    assert res.status_code == 200
    assert res.get_json()["unread_count"] == 1

    res = client.get("/api/notifications")
    assert res.status_code == 200
    items = res.get_json()["notifications"]
    assert any(n["title"] == "API Test" for n in items)

    res = client.post(f"/api/notifications/{nid}/read")
    assert res.status_code == 200

    res = client.get("/api/notifications/unread-count")
    assert res.get_json()["unread_count"] == 0

    res = client.get("/api/notifications/preferences")
    assert res.status_code == 200
    prefs = res.get_json()["preferences"]
    assert prefs["email_task_done"] is True


# ── Project / Folder / Asset Library tests ───────────────────────────────────


def test_project_crud_and_task_assignment():
    user = models.create_user(f"proj_{uuid.uuid4().hex}@example.com", "password123")
    other = models.create_user(f"proj_other_{uuid.uuid4().hex}@example.com", "password123")

    project = library.create_project(user.id, "Podcast Klip", "Klip dari podcast")
    assert project["name"] == "Podcast Klip"
    assert library.get_project(other.id, project["id"]) is None  # isolated

    updated = library.update_project(user.id, project["id"], name="Podcast Clips")
    assert updated["name"] == "Podcast Clips"
    assert library.list_projects(user.id)[0]["id"] == project["id"]

    task_id = f"proj-task-{uuid.uuid4().hex}"
    models.create_task(task_id, user_id=user.id, params={})
    assert library.assign_task_to_project(user.id, task_id, project["id"])
    tasks = library.list_project_tasks(user.id, project["id"])
    assert any(t["id"] == task_id for t in tasks)
    assert tasks[0]["project_id"] == project["id"]

    # Unassign
    assert library.assign_task_to_project(user.id, task_id, None)
    assert models.get_task(task_id, user_id=user.id)["project_id"] is None

    # Deleting the project detaches (not deletes) its tasks
    library.assign_task_to_project(user.id, task_id, project["id"])
    assert library.delete_project(user.id, project["id"])
    assert models.get_task(task_id, user_id=user.id) is not None
    assert models.get_task(task_id, user_id=user.id)["project_id"] is None
    assert library.get_project(user.id, project["id"]) is None
    models.delete_task(task_id)


def test_custom_asset_upload_list_delete():
    user = models.create_user(f"asset_lib_{uuid.uuid4().hex}@example.com", "password123")
    asset = library.save_asset(user.id, "bgm", "track.mp3", b"ID3audio", "audio/mpeg")
    assert asset["kind"] == "bgm"
    assert library.list_assets(user.id, "bgm")[0]["id"] == asset["id"]
    assert os.path.isfile(library.asset_path(user.id, asset["id"]))
    assert library.delete_asset(user.id, asset["id"])
    assert library.get_asset(user.id, asset["id"]) is None
    assert not os.path.isfile(os.path.join(secure_store.user_private_dir(user.id), "assets", asset["stored_filename"]))

    # Invalid kind / extension rejected
    with pytest.raises(ValueError):
        library.save_asset(user.id, "unknown", "x.bin", b"x")
    with pytest.raises(ValueError):
        library.save_asset(user.id, "bgm", "track.exe", b"x")


def test_saved_configs_presets_and_templates():
    user = models.create_user(f"cfg_{uuid.uuid4().hex}@example.com", "password123")
    preset = library.save_config(user.id, "preset", "Viral Subs", {
        "subtitle_enabled": True, "subtitle_style": "standard", "sub_fontsize": "22",
    })
    assert preset["data"]["subtitle_enabled"] is True
    # Upsert by name overwrites
    library.save_config(user.id, "preset", "Viral Subs", {"subtitle_enabled": False})
    assert library.get_config(user.id, "preset", "Viral Subs")["data"]["subtitle_enabled"] is False

    template = library.save_config(user.id, "template", "Reels 1080", {"output_resolution": "1080"})
    assert {c["name"] for c in library.list_configs(user.id, "preset")} == {"Viral Subs"}
    assert library.list_configs(user.id, "template")[0]["id"] == template["id"]
    assert library.delete_config(user.id, template["id"])
    assert library.list_configs(user.id, "template") == []
    with pytest.raises(ValueError):
        library.save_config(user.id, "bogus", "x", {})


def test_projects_api_endpoints():
    client, _ = _registered_client("library-api")
    res = client.post("/api/projects", json={"name": "API Project", "description": "via API"})
    assert res.status_code == 200
    project_id = res.get_json()["project"]["id"]

    res = client.get("/api/projects")
    assert res.status_code == 200
    assert any(p["id"] == project_id for p in res.get_json()["projects"])

    res = client.patch(f"/api/projects/{project_id}", json={"name": "Renamed"})
    assert res.status_code == 200
    assert res.get_json()["project"]["name"] == "Renamed"

    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    assert res.get_json()["project"]["name"] == "Renamed"

    res = client.delete(f"/api/projects/{project_id}")
    assert res.status_code == 200
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_assets_and_configs_api_endpoints():
    client, _ = _registered_client("lib-assets-api")
    res = client.post("/api/assets", data={
        "kind": "logo", "file": (__import__("io").BytesIO(b"\x89PNG\r\n\x1a\n"), "logo.png"),
    }, content_type="multipart/form-data")
    assert res.status_code == 200
    asset_id = res.get_json()["asset"]["id"]
    assert client.get("/api/assets").get_json()["assets"][0]["id"] == asset_id
    assert client.delete(f"/api/assets/{asset_id}").status_code == 200

    res = client.post("/api/configs/preset", json={"name": "API Preset", "data": {"sub_fontsize": "24"}})
    assert res.status_code == 200
    name = res.get_json()["config"]["name"]
    assert client.get("/api/configs/preset").get_json()["configs"][0]["name"] == name
    assert client.get(f"/api/configs/preset/{name}").status_code == 200


# ── Webhook & Integrations tests ─────────────────────────────────────────────


def test_webhook_crud_and_isolation():
    user = models.create_user(f"wh_{uuid.uuid4().hex}@example.com", "password123")
    other = models.create_user(f"wh_other_{uuid.uuid4().hex}@example.com", "password123")
    wh = webhooks.create_webhook(user.id, "https://example.com/hook", "s3cr3t", "clip.done")
    assert wh["url"] == "https://example.com/hook"
    assert webhooks.get_webhook(other.id, wh["id"]) is None

    updated = webhooks.update_webhook(user.id, wh["id"], is_active=False)
    assert updated["is_active"] == 0
    assert webhooks.delete_webhook(user.id, wh["id"])
    with pytest.raises(ValueError):
        webhooks.create_webhook(user.id, "ftp://bad", "s")


def test_webhook_event_filtering_and_signature():
    user = models.create_user(f"wh_evt_{uuid.uuid4().hex}@example.com", "password123")
    wh_done = webhooks.create_webhook(user.id, "https://done.example/hook", "secret-done", "clip.done")
    wh_all = webhooks.create_webhook(user.id, "https://all.example/hook", "secret-all", "*")
    wh_err = webhooks.create_webhook(user.id, "https://err.example/hook", "secret-err", "clip.error")

    queued = webhooks.dispatch_event(user.id, "clip.done", {"task_id": "t1"})
    assert queued == 2  # wh_done + wh_all match; wh_err does not

    deliveries = webhooks.list_deliveries(user.id)
    events = {d["event"] for d in deliveries}
    assert events == {"clip.done"}
    assert {d["webhook_id"] for d in deliveries} == {wh_done["id"], wh_all["id"]}

    # Signature is HMAC-SHA256 of the body with the webhook secret.
    import hmac as _hmac, hashlib as _hl
    body = deliveries[0]["payload"].encode("utf-8")
    secret = webhooks.get_webhook(user.id, deliveries[0]["webhook_id"])["secret"]
    expected = _hmac.new(secret.encode("utf-8"), body, _hl.sha256).hexdigest()
    assert webhooks._sign(secret, body) == expected

    for w in (wh_done, wh_all, wh_err):
        webhooks.delete_webhook(user.id, w["id"])


def test_webhook_delivery_retry_backoff():
    user = models.create_user(f"wh_retry_{uuid.uuid4().hex}@example.com", "password123")
    wh = webhooks.create_webhook(user.id, "https://nope.invalid.example.hook", "secret", "*")
    webhooks.dispatch_event(user.id, "clip.error", {"task_id": "t-err", "error": "boom"})

    # Force an immediate sweep; the bogus URL must fail and be marked for retry.
    webhooks.process_pending(limit=10)
    deliveries = webhooks.list_deliveries(user.id)
    assert deliveries
    d = deliveries[0]
    assert d["status"] in ("retry", "failed")
    assert d["attempts"] >= 1
    assert d["last_error"]
    webhooks.delete_webhook(user.id, wh["id"])


def test_webhooks_api_endpoints():
    client, _ = _registered_client("wh-api")
    res = client.post("/api/webhooks", json={"url": "https://hook.example/cb", "secret": "s", "events": "clip.done"})
    assert res.status_code == 200
    wid = res.get_json()["webhook"]["id"]
    assert client.get("/api/webhooks").get_json()["webhooks"][0]["id"] == wid
    assert client.patch(f"/api/webhooks/{wid}", json={"is_active": False}).status_code == 200
    assert client.get(f"/api/webhooks/{wid}/deliveries").status_code == 200
    assert client.delete(f"/api/webhooks/{wid}").status_code == 200


# ── API for Developers tests ─────────────────────────────────────────────────


def test_api_key_create_verify_revoke():
    user = models.create_user(f"key_{uuid.uuid4().hex}@example.com", "password123")
    created = apikeys.create_api_key(user.id, "CI Pipeline")
    assert created["key"].startswith("ck_live_")
    assert apikeys.verify_api_key(created["key"]) == user.id
    # Wrong/foreign key rejected
    assert apikeys.verify_api_key("ck_live_notarealkey") is None
    assert apikeys.verify_api_key("not-a-clipper-key") is None

    key_id = created["id"]
    assert apikeys.list_api_keys(user.id)[0]["id"] == key_id
    # Plaintext is not persisted; only the hash is.
    with models._connect() as conn:
        row = conn.execute("SELECT key_hash FROM api_keys WHERE id = ?", (key_id,)).fetchone()
    assert created["key"] not in row["key_hash"]

    assert apikeys.set_api_key_active(user.id, key_id, False)
    assert apikeys.verify_api_key(created["key"]) is None  # disabled
    assert apikeys.revoke_api_key(user.id, key_id)
    assert apikeys.list_api_keys(user.id) == []


def test_developer_api_clip_lifecycle():
    client = _auth_client()
    email = f"dev_{uuid.uuid4().hex}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "password123", "name": "Dev"})
    user_id = models.get_user_by_email(email).id

    created = apikeys.create_api_key(user_id, "Dev Key")
    headers = {"Authorization": f"Bearer {created['key']}"}

    # Unauthenticated request rejected
    assert client.get("/api/v1/tasks").status_code == 401
    # Bad key rejected
    assert client.get("/api/v1/tasks", headers={"Authorization": "Bearer ck_live_bad"}).status_code == 401

    # List tasks via API key
    res = client.get("/api/v1/tasks", headers=headers)
    assert res.status_code == 200
    assert "tasks" in res.get_json()

    # Create a task row directly, then fetch its status and delete via API.
    task_id = f"dev-task-{uuid.uuid4().hex}"
    models.create_task(task_id, user_id=user_id, params={"url": "https://x", "start": "0", "end": "5"})
    res = client.get(f"/api/v1/clip/{task_id}", headers=headers)
    assert res.status_code == 200
    assert res.get_json()["task_id"] == task_id

    # Another user's API key must not see this task.
    other_email = f"dev_other_{uuid.uuid4().hex}@example.com"
    client.post("/api/auth/register", json={"email": other_email, "password": "password123", "name": "Other"})
    other_id = models.get_user_by_email(other_email).id
    other_key = apikeys.create_api_key(other_id, "Other Key")["key"]
    assert client.get(f"/api/v1/clip/{task_id}", headers={"Authorization": f"Bearer {other_key}"}).status_code == 404

    # Delete via API
    assert client.delete(f"/api/v1/clip/{task_id}", headers=headers).status_code == 200
    assert models.get_task(task_id, user_id=user_id) is None


def test_api_keys_management_api():
    client, user_id = _registered_client("keys-api")
    res = client.post("/api/keys", json={"name": "Test Key"})
    assert res.status_code == 200
    data = res.get_json()["key"]
    assert data["key"].startswith("ck_live_")
    key_id = data["id"]

    res = client.get("/api/keys")
    assert res.status_code == 200
    assert any(k["id"] == key_id for k in res.get_json()["keys"])
    # The plaintext must not come back on list.
    assert "key" not in res.get_json()["keys"][0]

    assert client.patch(f"/api/keys/{key_id}", json={"is_active": False}).status_code == 200
    assert client.delete(f"/api/keys/{key_id}").status_code == 200
