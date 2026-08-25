"""webhooks.py — Per-user outbound webhook endpoints.

Features:
  * Register multiple webhook URLs per user with a shared secret.
  * Event filtering (`*` = all, or comma-separated `clip.done,clip.error`).
  * HMAC-SHA256 signature in the `X-Clipper-Signature` header so receivers
    can verify authenticity and integrity.
  * Durable delivery queue backed by SQLite (`webhook_deliveries`).
  * Retry with exponential backoff up to MAX_ATTEMPTS, driven by a background
    sweeper thread.

Events are emitted from the task queue completion hook (see task_queue.py):
    dispatch_event(user_id, "clip.done",  {"task_id": ..., ...})
    dispatch_event(user_id, "clip.error", {"task_id": ..., "error": ...})
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

import models

logger = logging.getLogger("clipper")

# Retry policy: 5 attempts → immediate, 30s, 2m, 8m, 30m.
MAX_ATTEMPTS = int(os.environ.get("WEBHOOK_MAX_ATTEMPTS", "5"))
BACKOFF_SECONDS = [0, 30, 120, 480, 1800]
DELIVERY_TIMEOUT = int(os.environ.get("WEBHOOK_TIMEOUT", "10"))
SWEEP_INTERVAL = int(os.environ.get("WEBHOOK_SWEEP_INTERVAL", "15"))

_SUPPORTED_EVENTS = {"clip.done", "clip.error"}
_sweeper_started = False
_sweeper_lock = threading.Lock()


# ── CRUD ─────────────────────────────────────────────────────────────────────


def create_webhook(user_id: int, url: str, secret: str, events: str = "*") -> dict:
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("URL webhook harus http(s)://")
    secret = (secret or "").strip()
    if not secret:
        raise ValueError("Secret wajib diisi untuk verifikasi signature.")
    events = (events or "*").strip() or "*"
    now = models._now()
    with models._connect() as conn:
        cur = conn.execute(
            """INSERT INTO webhooks (user_id, url, secret, events, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (user_id, url, secret, events, now, now),
        )
        conn.commit()
        return get_webhook(user_id, cur.lastrowid)


def get_webhook(user_id: int, webhook_id: int) -> Optional[dict]:
    with models._connect() as conn:
        row = conn.execute(
            "SELECT * FROM webhooks WHERE id = ? AND user_id = ?",
            (webhook_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def list_webhooks(user_id: int) -> list:
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM webhooks WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_webhook(user_id: int, webhook_id: int, url: Optional[str] = None,
                   events: Optional[str] = None, is_active: Optional[bool] = None) -> Optional[dict]:
    fields, values = [], []
    if url is not None:
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("URL webhook harus http(s)://")
        fields.append("url = ?"); values.append(url)
    if events is not None:
        fields.append("events = ?"); values.append((events or "*").strip() or "*")
    if is_active is not None:
        fields.append("is_active = ?"); values.append(int(bool(is_active)))
    if not fields:
        return get_webhook(user_id, webhook_id)
    fields.append("updated_at = ?"); values.append(models._now())
    values.extend([webhook_id, user_id])
    with models._connect() as conn:
        cur = conn.execute(
            f"UPDATE webhooks SET {', '.join(fields)} WHERE id = ? AND user_id = ?", values,
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_webhook(user_id, webhook_id)


def delete_webhook(user_id: int, webhook_id: int) -> bool:
    with models._connect() as conn:
        cur = conn.execute("DELETE FROM webhooks WHERE id = ? AND user_id = ?", (webhook_id, user_id))
        conn.commit()
        return cur.rowcount > 0


# ── Dispatch + delivery ──────────────────────────────────────────────────────


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _events_match(configured: str, event: str) -> bool:
    configured = (configured or "*").strip()
    if configured == "*":
        return True
    return event in {e.strip() for e in configured.split(",") if e.strip()}


def dispatch_event(user_id: int, event: str, payload: dict) -> int:
    """Queue a delivery row for every active, matching webhook of the user.

    Returns the number of deliveries queued. Called from the task completion hook;
    never raises so the task flow is unaffected by webhook misconfiguration.
    """
    if event not in _SUPPORTED_EVENTS:
        return 0
    try:
        body = json.dumps({"event": event, "payload": payload}, separators=(",", ":"))
        now = models._now()
        with models._connect() as conn:
            rows = conn.execute(
                "SELECT id, url, secret, events FROM webhooks WHERE user_id = ? AND is_active = 1",
                (user_id,),
            ).fetchall()
            queued = 0
            for r in rows:
                if not _events_match(r["events"], event):
                    continue
                conn.execute(
                    """INSERT INTO webhook_deliveries
                       (webhook_id, event, payload, status, attempts, created_at, updated_at, next_attempt_at)
                       VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)""",
                    (r["id"], event, body, now, now, now),
                )
                queued += 1
            conn.commit()
        return queued
    except Exception:
        logger.exception("Failed to queue webhook deliveries for user %s event %s", user_id, event)
        return 0


def _attempt_delivery(delivery: dict, webhook_url: str, webhook_secret: str) -> bool:
    """Perform one HTTP POST attempt. Returns True if delivered (2xx)."""
    body = delivery["payload"].encode("utf-8")
    signature = _sign(webhook_secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-Clipper-Signature": "sha256=" + signature,
        "X-Clipper-Event": delivery["event"],
    }
    try:
        resp = requests.post(webhook_url, data=body, headers=headers, timeout=DELIVERY_TIMEOUT)
        ok = 200 <= resp.status_code < 300
        _record_attempt(delivery["id"], ok, resp.status_code, "" if ok else f"HTTP {resp.status_code}")
        return ok
    except Exception as exc:
        _record_attempt(delivery["id"], False, None, str(exc)[:500])
        return False


def _record_attempt(delivery_id: int, ok: bool, status_code: Optional[int], error: str) -> None:
    now = models._now()
    with models._connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM webhook_deliveries WHERE id = ?",
            (delivery_id,),
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        if ok or attempts >= MAX_ATTEMPTS:
            status = "delivered" if ok else "failed"
            next_at = None
        else:
            delay = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS) - 1)]
            status = "retry"
            next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        conn.execute(
            """UPDATE webhook_deliveries
               SET status = ?, attempts = ?, last_status_code = ?, last_error = ?,
                   next_attempt_at = ?, updated_at = ?
               WHERE id = ?""",
            (status, attempts, status_code, error, next_at, now, delivery_id),
        )
        conn.commit()


def process_pending(limit: int = 25) -> int:
    """Run one sweep of due deliveries. Returns the number attempted."""
    now = models._now()
    with models._connect() as conn:
        rows = conn.execute(
            """SELECT d.id, d.webhook_id, d.event, d.payload, d.attempts
               FROM webhook_deliveries d
               JOIN webhooks w ON w.id = d.webhook_id
               WHERE d.status IN ('pending', 'retry')
                 AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
                 AND w.is_active = 1
               ORDER BY d.created_at ASC LIMIT ?""",
            (now, limit),
        ).fetchall()
        deliveries = [dict(r) for r in rows]
        # Mark in-flight so a concurrent sweeper doesn't double-run.
        for d in deliveries:
            conn.execute(
                "UPDATE webhook_deliveries SET updated_at = ? WHERE id = ?",
                (now, d["id"]),
            )
        conn.commit()

    attempted = 0
    for d in deliveries:
        webhook = None
        with models._connect() as conn:
            wh = conn.execute("SELECT url, secret FROM webhooks WHERE id = ?", (d["webhook_id"],)).fetchone()
            webhook = dict(wh) if wh else None
        if not webhook:
            _record_attempt(d["id"], False, None, "webhook deleted")
            attempted += 1
            continue
        _attempt_delivery(d, webhook["url"], webhook["secret"])
        attempted += 1
    return attempted


def start_sweeper() -> None:
    """Start a single background thread that drains the delivery queue."""
    global _sweeper_started
    with _sweeper_lock:
        if _sweeper_started:
            return
        _sweeper_started = True

        def _loop():
            while True:
                try:
                    process_pending()
                except Exception:
                    logger.exception("Webhook sweeper iteration failed")
                time.sleep(SWEEP_INTERVAL)

        threading.Thread(target=_loop, name="webhook-sweeper", daemon=True).start()


def list_deliveries(user_id: int, webhook_id: Optional[int] = None, limit: int = 50) -> list:
    """Return delivery history for the user's webhooks (newest first)."""
    with models._connect() as conn:
        if webhook_id is not None:
            rows = conn.execute(
                """SELECT d.* FROM webhook_deliveries d
                   JOIN webhooks w ON w.id = d.webhook_id
                   WHERE d.webhook_id = ? AND w.user_id = ?
                   ORDER BY d.created_at DESC LIMIT ?""",
                (webhook_id, user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT d.* FROM webhook_deliveries d
                   JOIN webhooks w ON w.id = d.webhook_id
                   WHERE w.user_id = ?
                   ORDER BY d.created_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
