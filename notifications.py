"""notifications.py — Email delivery and in-app notifications for Clipper Studio.

This module provides:
  * Async SMTP email sending (falls back to logging when SMTP is not configured).
  * HTML email templates for common SaaS lifecycle events.
  * SQLite-backed in-app notifications and user preferences.
  * High-level trigger functions wired into the rest of the application.

Emails are sent in a background thread so that clip workers and HTTP handlers
are never blocked by SMTP latency.
"""

from __future__ import annotations

import logging
import os
import smtplib
import threading
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional
from urllib.parse import urljoin

import models

logger = logging.getLogger("clipper")


# ── Configuration helpers ────────────────────────────────────────────────────


def _smtp_config():
    """Return current SMTP configuration from environment variables."""
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_addr": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", ""),
        "from_name": os.environ.get("SMTP_FROM_NAME", "Clipper Studio"),
        "tls": os.environ.get("SMTP_TLS", "true").lower() == "true",
        "public_base_url": os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000"),
    }


# ── Low-level email transport ────────────────────────────────────────────────


def _send_email_sync(to: str, subject: str, html_body: str) -> bool:
    """Send an email synchronously using the configured SMTP server."""
    config = _smtp_config()
    host = config["host"]
    if not host:
        logger.warning("SMTP_HOST not configured; email to %s was not sent.", to)
        return False

    from_addr = config["from_addr"]
    from_name = config["from_name"]
    sender = formataddr((from_name, from_addr)) if from_name else from_addr

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    try:
        if config["tls"]:
            server = smtplib.SMTP(host, config["port"], timeout=15)
            server.starttls()
        else:
            server = smtplib.SMTP(host, config["port"], timeout=15)

        user = config["user"]
        password = config["password"]
        if user and password:
            server.login(user, password)

        server.sendmail(from_addr, [to], msg.as_string())
        server.quit()
        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception as exc:
        logger.exception("Failed to send email to %s: %s", to, exc)
        return False


def send_email_async(to: str, subject: str, html_body: str) -> None:
    """Send an email in a background thread so callers are not blocked."""
    config = _smtp_config()
    if not config["host"]:
        logger.warning("SMTP_HOST not configured; email to %s was not sent.", to)
        return
    thread = threading.Thread(
        target=_send_email_sync,
        args=(to, subject, html_body),
        name=f"email-{to}",
        daemon=True,
    )
    thread.start()


# ── Minimal HTML template rendering ──────────────────────────────────────────


def _render(template: str, **context) -> str:
    """Very simple string-template renderer; keeps us dependency-free."""
    return template.format(**context)


_BASE_STYLE = """
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #111; line-height: 1.6; }
  .container { max-width: 560px; margin: 0 auto; padding: 24px; border: 3px solid #000; }
  h1 { font-size: 22px; margin-bottom: 16px; }
  p { margin: 12px 0; }
  .btn { display: inline-block; padding: 12px 24px; background: #000; color: #fff; text-decoration: none; text-transform: uppercase; letter-spacing: 1px; font-weight: bold; }
  .footer { margin-top: 32px; font-size: 12px; color: #666; }
</style>
"""


def _wrap_body(title: str, content: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
{_BASE_STYLE}
</head>
<body>
<div class="container">
{content}
<p class="footer">Clipper Studio — Email otomatis, mohon tidak membalas email ini.</footer>
</div>
</body>
</html>
"""


# ── Email templates and senders ──────────────────────────────────────────────


def send_welcome_email(user) -> None:
    """Send a welcome email to a newly registered user."""
    config = _smtp_config()
    dashboard_url = config["public_base_url"]
    content = f"""
<h1>Selamat datang di Clipper Studio, {user.name or user.email}!</h1>
<p>Terima kasih telah mendaftar. Anda sekarang bisa mulai mengubah video panjang menjadi klip viral siap publikasi.</p>
<p><a href="{dashboard_url}" class="btn">Buka Dashboard</a></p>
<p>Tip cepat: paste URL video, pilih segmen menarik, lalu klik Generate Clip.</p>
"""
    send_email_async(user.email, "Selamat Datang di Clipper Studio", _wrap_body("Selamat Datang", content))


def _task_download_link(task: dict) -> str:
    """Build a public download link for a finished clip task."""
    config = _smtp_config()
    try:
        # cloud_storage is imported lazily to avoid circular imports at module load.
        import cloud_storage
        urls = cloud_storage.asset_urls(task["id"], task.get("user_id"))
        return urls.get("download_url") or urls.get("clip_url") or ""
    except Exception:
        return ""


def send_task_complete_email(user, task: dict) -> None:
    """Send a task-finished email with a download link."""
    config = _smtp_config()
    download_link = _task_download_link(task) or urljoin(
        config["public_base_url"], f"/download/{task.get('output_file', '')}"
    )
    detail_link = urljoin(config["public_base_url"], f"/task/{task['id']}")
    content = f"""
<h1>Clip Anda sudah selesai!</h1>
<p>Halo {user.name or user.email},</p>
<p>Task <strong>#{task['id'][:8]}</strong> telah selesai diproses dan siap diunduh.</p>
<p><a href="{download_link}" class="btn">Unduh Clip</a></p>
<p>Atau lihat detail di dashboard: <a href="{detail_link}">{detail_link}</a></p>
"""
    send_email_async(user.email, "Clip Selesai — Siap Diunduh", _wrap_body("Clip Selesai", content))


def send_task_failed_email(user, task: dict) -> None:
    """Send a task-failed email so the user knows the clip did not finish."""
    config = _smtp_config()
    detail_link = urljoin(config["public_base_url"], f"/task/{task['id']}")
    error_text = (task.get("error") or "Terjadi kesalahan saat memproses clip.").strip()
    content = f"""
<h1>Clip gagal diproses</h1>
<p>Halo {user.name or user.email},</p>
<p>Mohon maaf, task <strong>#{task['id'][:8]}</strong> tidak berhasil diselesaikan.</p>
<p><strong>Error:</strong> {error_text}</p>
<p>Silakan coba lagi atau hubungi tim support kami.</p>
<p><a href="{detail_link}" class="btn">Lihat Detail</a></p>
"""
    send_email_async(user.email, "Clip Gagal Diproses", _wrap_body("Clip Gagal", content))


def send_quota_alert_email(user, metric: str, usage_summary: dict, threshold: int) -> None:
    """Send a quota threshold alert (80% or 100%)."""
    config = _smtp_config()
    metric_data = usage_summary.get("metrics", {}).get(metric, {})
    used = metric_data.get("used", 0)
    limit = metric_data.get("limit", 0)
    percent = int((used / limit * 100)) if limit else 0
    plans_url = urljoin(config["public_base_url"], "/?upgrade=1")

    if threshold >= 100:
        subject = f"Kuota {metric} Habis"
        headline = f"Kuota {metric} bulan ini sudah habis"
        body_text = f"Anda telah menggunakan {used} dari {limit} kuota {metric}. Upgrade plan untuk melanjutkan."
    else:
        subject = f"Kuota {metric} Hampir Habis ({percent}%)"
        headline = f"Kuota {metric} sudah mencapai {percent}%"
        body_text = f"Anda telah menggunakan {used} dari {limit} kuota {metric}. Segera upgrade agar tidak terhenti."

    content = f"""
<h1>{headline}</h1>
<p>Halo {user.name or user.email},</p>
<p>{body_text}</p>
<p><a href="{plans_url}" class="btn">Upgrade Plan</a></p>
"""
    send_email_async(user.email, subject, _wrap_body("Kuota", content))


def send_payment_receipt_email(user, invoice: dict) -> None:
    """Send a payment receipt after a successful invoice payment."""
    config = _smtp_config()
    amount = invoice.get("amount", 0)
    currency = invoice.get("currency", "IDR")
    plan = invoice.get("plan_code", "Pro").capitalize()
    invoice_id = invoice.get("id", "")
    subscription_url = urljoin(config["public_base_url"], "/?tab=subscription")
    content = f"""
<h1>Pembayaran Berhasil</h1>
<p>Halo {user.name or user.email},</p>
<p>Terima kasih atas pembayaran Anda. Berikut detailnya:</p>
<ul>
  <li>Invoice: <strong>{invoice_id}</strong></li>
  <li>Plan: <strong>{plan}</strong></li>
  <li>Total: <strong>{amount} {currency}</strong></li>
</ul>
<p><a href="{subscription_url}" class="btn">Lihat Subscription</a></p>
"""
    send_email_async(user.email, "Pembayaran Berhasil — Clipper Studio", _wrap_body("Receipt", content))


def send_payment_failed_email(user, invoice: dict) -> None:
    """Send a failed-payment reminder."""
    config = _smtp_config()
    invoice_id = invoice.get("id", "")
    checkout_url = invoice.get("checkout_url") or urljoin(config["public_base_url"], "/?tab=billing")
    content = f"""
<h1>Pembayaran Gagal</h1>
<p>Halo {user.name or user.email},</p>
<p>Kami tidak berhasil memproses pembayaran untuk invoice <strong>{invoice_id}</strong>.</p>
<p>Silakan coba lagi untuk menghindari gangguan pada subscription Anda.</p>
<p><a href="{checkout_url}" class="btn">Bayar Sekarang</a></p>
"""
    send_email_async(user.email, "Pembayaran Gagal — Clipper Studio", _wrap_body("Pembayaran Gagal", content))


# ── In-app notifications (SQLite-backed) ─────────────────────────────────────


def create_notification(
    user_id: int,
    title: str,
    body: Optional[str] = None,
    link: Optional[str] = None,
    kind: str = "info",
) -> int:
    """Create an in-app notification and return its id."""
    prefs = models.get_notification_preferences(user_id)
    if not prefs.get("in_app_enabled", True):
        return -1

    now = datetime.now(timezone.utc).isoformat()
    with models._connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO notifications (user_id, title, body, link, kind, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (user_id, title, body, link, kind, now),
        )
        conn.commit()
        return cur.lastrowid


def list_notifications(user_id: int, limit: int = 50, unread_only: bool = False) -> list:
    """Return notifications for a user, newest first."""
    with models._connect() as conn:
        where = "WHERE user_id = ?"
        params = [user_id]
        if unread_only:
            where += " AND is_read = 0"
        rows = conn.execute(
            f"""
            SELECT id, user_id, title, body, link, kind, is_read, created_at
            FROM notifications {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        result = [dict(r) for r in rows]
        for item in result:
            item["is_read"] = bool(item["is_read"])
        return result


def mark_notification_read(notification_id: int, user_id: int) -> bool:
    """Mark a single notification as read, scoped to the owner."""
    with models._connect() as conn:
        cur = conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def mark_all_notifications_read(user_id: int) -> int:
    """Mark all notifications for a user as read."""
    with models._connect() as conn:
        cur = conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
            (user_id,),
        )
        conn.commit()
        return cur.rowcount


def count_unread_notifications(user_id: int) -> int:
    """Return the unread notification count for a user."""
    with models._connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user_id,),
        ).fetchone()
        return row[0] if row else 0


# ── Idempotency log ──────────────────────────────────────────────────────────


def _log_event(user_id: int, channel: str, event_key: str) -> bool:
    """Record that a notification was sent. Returns True on first send, False if duplicate."""
    now = datetime.now(timezone.utc).isoformat()
    with models._connect() as conn:
        try:
            conn.execute(
                "INSERT INTO notification_log (user_id, channel, event_key, created_at) VALUES (?, ?, ?, ?)",
                (user_id, channel, event_key, now),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False


def _was_logged(user_id: int, channel: str, event_key: str) -> bool:
    """Check whether a notification event has already been logged."""
    with models._connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notification_log WHERE user_id = ? AND channel = ? AND event_key = ?",
            (user_id, channel, event_key),
        ).fetchone()
        return row is not None


# ── High-level trigger functions ─────────────────────────────────────────────


def notify_task_completed(user_id: Optional[int], task_id: str) -> None:
    """Notify a user that their clip task finished successfully."""
    if not user_id:
        return
    task = models.get_task(task_id, user_id=user_id)
    if not task or task.get("status") != "done":
        return

    title = "Clip selesai diproses"
    body = f"Task #{task_id[:8]} sudah selesai dan siap diunduh."
    link = f"/task/{task_id}"
    create_notification(user_id, title, body=body, link=link, kind="success")

    prefs = models.get_notification_preferences(user_id)
    if prefs.get("email_task_done", True):
        user = models.get_user_by_id(user_id)
        if user:
            send_task_complete_email(user, task)


def notify_task_failed(user_id: Optional[int], task_id: str) -> None:
    """Notify a user that their clip task failed."""
    if not user_id:
        return
    task = models.get_task(task_id, user_id=user_id)
    if not task or task.get("status") != "error":
        return

    title = "Clip gagal diproses"
    body = f"Task #{task_id[:8]} tidak berhasil diselesaikan."
    link = f"/task/{task_id}"
    create_notification(user_id, title, body=body, link=link, kind="error")

    prefs = models.get_notification_preferences(user_id)
    if prefs.get("email_task_done", True):
        user = models.get_user_by_id(user_id)
        if user:
            send_task_failed_email(user, task)


def notify_quota_alert(user_id: int, metric: str, threshold: int) -> None:
    """Send a one-time quota alert per (user, metric, period, threshold)."""
    import saas

    period = saas.period_key()
    event_key = f"{period}:{metric}:{threshold}"
    if _was_logged(user_id, "email:quota", event_key):
        return

    summary = saas.usage_summary(user_id)
    metric_data = summary.get("metrics", {}).get(metric, {})
    used = metric_data.get("used", 0)
    limit = metric_data.get("limit", 0)
    ratio = used / limit if limit else 0

    threshold_reached = threshold >= 100 and used >= limit
    if not threshold_reached:
        threshold_reached = ratio >= (threshold / 100.0)

    if not threshold_reached:
        return

    # Log first so concurrent callers only send once.
    if not _log_event(user_id, "email:quota", event_key):
        return

    create_notification(
        user_id,
        f"Kuota {metric} {'habis' if threshold >= 100 else f'sudah {threshold}%'}",
        body=f"Anda telah menggunakan {used} dari {limit} kuota {metric}.",
        link="/?upgrade=1",
        kind="warning" if threshold < 100 else "error",
    )

    prefs = models.get_notification_preferences(user_id)
    if prefs.get("email_quota_alert", True):
        user = models.get_user_by_id(user_id)
        if user:
            send_quota_alert_email(user, metric, summary, threshold)


def notify_payment_receipt(user_id: int, invoice_id: str) -> None:
    """Notify a user that a payment succeeded."""
    import billing

    invoice = next((inv for inv in billing.list_invoices(user_id) if inv["id"] == invoice_id), None)
    if not invoice or invoice.get("status") != "paid":
        return

    create_notification(
        user_id,
        "Pembayaran berhasil",
        body=f"Invoice {invoice_id} telah dibayar.",
        link="/?tab=subscription",
        kind="success",
    )

    prefs = models.get_notification_preferences(user_id)
    if prefs.get("email_payment", True):
        user = models.get_user_by_id(user_id)
        if user:
            send_payment_receipt_email(user, invoice)


def notify_payment_failed(user_id: int, invoice_id: str) -> None:
    """Notify a user that a payment failed."""
    import billing

    invoice = next((inv for inv in billing.list_invoices(user_id) if inv["id"] == invoice_id), None)
    if not invoice or invoice.get("status") != "failed":
        return

    create_notification(
        user_id,
        "Pembayaran gagal",
        body=f"Invoice {invoice_id} tidak berhasil diproses.",
        link=invoice.get("checkout_url") or "/?tab=billing",
        kind="error",
    )

    prefs = models.get_notification_preferences(user_id)
    if prefs.get("email_payment", True):
        user = models.get_user_by_id(user_id)
        if user:
            send_payment_failed_email(user, invoice)


def notify_welcome(user_id: int) -> None:
    """Send welcome email and in-app onboarding notification."""
    user = models.get_user_by_id(user_id)
    if not user:
        return

    create_notification(
        user_id,
        "Selamat datang di Clipper Studio",
        body="Mulai buat klip viral dari video panjang Anda.",
        link="/?tab=manual",
        kind="info",
    )

    send_welcome_email(user)
