"""Plans, subscriptions, monthly usage accounting, and rate limiting."""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import models


PLANS = {
    "free": {
        "name": "Free", "price": 0, "currency": "IDR", "trial_days": 0,
        "limits": {"clip_count": 5, "render_minutes": 30, "ai_scan": 5, "ai_copy": 10},
    },
    "pro": {
        "name": "Pro", "price": 99000, "currency": "IDR", "trial_days": 7,
        "limits": {"clip_count": 100, "render_minutes": 600, "ai_scan": 100, "ai_copy": 200},
    },
    "team": {
        "name": "Team", "price": 299000, "currency": "IDR", "trial_days": 7,
        "limits": {"clip_count": 400, "render_minutes": 2400, "ai_scan": 400, "ai_copy": 800},
    },
    "agency": {
        "name": "Agency", "price": 799000, "currency": "IDR", "trial_days": 14,
        "limits": {"clip_count": 1500, "render_minutes": 9000, "ai_scan": 1500, "ai_copy": 3000},
    },
}


def period_key(now=None):
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def parse_duration_minutes(start, end):
    def seconds(value):
        parts = str(value).strip().split(":")
        try:
            values = [float(part) for part in parts]
        except ValueError:
            return 0.0
        if len(values) == 3:
            return values[0] * 3600 + values[1] * 60 + values[2]
        if len(values) == 2:
            return values[0] * 60 + values[1]
        return values[0] if values else 0.0
    return max(0.0, seconds(end) - seconds(start)) / 60.0


def get_subscription(user_id):
    with models._connect() as conn:
        row = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return {
            "user_id": user_id, "plan_code": "free", "status": "active",
            "cancel_at_period_end": False, "paused_at": None,
        }
    result = dict(row)
    result["cancel_at_period_end"] = bool(result["cancel_at_period_end"])
    end = result.get("current_period_end")
    expired = end and end < datetime.now(timezone.utc).isoformat()
    if expired and (result["status"] not in ("active", "trialing") or result["cancel_at_period_end"]):
        result["plan_code"] = "free"
        result["status"] = "expired"
    return result


def get_plan(user_id):
    subscription = get_subscription(user_id)
    if subscription.get("status") in ("active", "trialing") and not subscription.get("paused_at"):
        return PLANS.get(subscription.get("plan_code"), PLANS["free"])
    return PLANS["free"]


def usage_summary(user_id):
    subscription = get_subscription(user_id)
    plan_code = subscription.get("plan_code", "free") if subscription.get("status") in ("active", "trialing") else "free"
    plan = PLANS.get(plan_code, PLANS["free"])
    with models._connect() as conn:
        rows = conn.execute(
            """SELECT metric, COALESCE(SUM(quantity), 0) AS used
               FROM usage_events WHERE user_id = ? AND period_key = ? GROUP BY metric""",
            (user_id, period_key()),
        ).fetchall()
    used = {row["metric"]: float(row["used"]) for row in rows}
    metrics = {}
    for metric, limit in plan["limits"].items():
        value = used.get(metric, 0)
        metrics[metric] = {"used": round(value, 2), "limit": limit, "remaining": max(0, round(limit - value, 2))}
    return {"period": period_key(), "plan_code": plan_code, "plan": plan["name"], "metrics": metrics}


def _is_admin(user_id):
    """Return True if the user has the admin flag set."""
    if not user_id:
        return False
    user = models.get_user_by_id(user_id)
    return bool(user and user.is_admin)


def consume_usage(user_id, metric, quantity, idempotency_key, task_id=None):
    """Atomically enforce and consume a monthly plan allowance."""
    if quantity <= 0:
        return usage_summary(user_id)
    plan = get_plan(user_id)
    limit = plan["limits"].get(metric)
    if limit is None:
        raise ValueError("Metrik penggunaan tidak dikenal.")
    with models._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT 1 FROM usage_events WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            conn.rollback()
            return usage_summary(user_id)
        row = conn.execute(
            """SELECT COALESCE(SUM(quantity), 0) AS used FROM usage_events
               WHERE user_id = ? AND period_key = ? AND metric = ?""",
            (user_id, period_key(), metric),
        ).fetchone()
        used = float(row["used"] or 0)
        if used + quantity > limit and not _is_admin(user_id):
            conn.rollback()
            raise PermissionError(f"Kuota {metric} bulan ini telah habis.")
        conn.execute(
            """INSERT INTO usage_events
               (user_id, task_id, metric, quantity, period_key, idempotency_key, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, task_id, metric, quantity, period_key(), idempotency_key, models._now()),
        )
        conn.commit()
    return usage_summary(user_id)


def consume_task_usage(user_id, task_id, start, end):
    consume_usage(user_id, "clip_count", 1, f"task:{task_id}:clip", task_id)
    try:
        return consume_usage(
            user_id, "render_minutes", parse_duration_minutes(start, end),
            f"task:{task_id}:minutes", task_id,
        )
    except Exception:
        with models._connect() as conn:
            conn.execute("DELETE FROM usage_events WHERE idempotency_key = ?", (f"task:{task_id}:clip",))
            conn.commit()
        raise


def consume_batch_task_usage(user_id, tasks):
    """Reserve all batch clip and render usage in one SQLite transaction."""
    plan = get_plan(user_id)
    quantities = {
        "clip_count": float(len(tasks)),
        "render_minutes": sum(parse_duration_minutes(item["start"], item["end"]) for item in tasks),
    }
    key = period_key()
    with models._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for metric, quantity in quantities.items():
            used = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM usage_events WHERE user_id = ? AND period_key = ? AND metric = ?",
                (user_id, key, metric),
            ).fetchone()[0]
            if float(used or 0) + quantity > plan["limits"][metric] and not _is_admin(user_id):
                conn.rollback()
                raise PermissionError(f"Kuota {metric} bulan ini telah habis.")
        for item in tasks:
            values = (
                ("clip_count", 1, f"task:{item['task_id']}:clip"),
                ("render_minutes", parse_duration_minutes(item["start"], item["end"]), f"task:{item['task_id']}:minutes"),
            )
            for metric, quantity, idempotency in values:
                conn.execute(
                    """INSERT INTO usage_events
                       (user_id, task_id, metric, quantity, period_key, idempotency_key, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, item["task_id"], metric, quantity, key, idempotency, models._now()),
                )
        conn.commit()
    return usage_summary(user_id)


def set_subscription(user_id, plan_code, status="active", provider="manual", provider_reference=None,
                     trial_end=None, current_period_end=None, cancel_at_period_end=False, paused=False):
    if plan_code not in PLANS:
        raise ValueError("Plan tidak dikenal.")
    now = datetime.now(timezone.utc)
    period_end = current_period_end or (now + timedelta(days=30)).isoformat()
    with models._connect() as conn:
        conn.execute(
            """INSERT INTO subscriptions
               (user_id, plan_code, status, provider, provider_reference, trial_end,
                current_period_start, current_period_end, cancel_at_period_end, paused_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET plan_code=excluded.plan_code,
                 status=excluded.status, provider=excluded.provider,
                 provider_reference=COALESCE(excluded.provider_reference, subscriptions.provider_reference),
                 trial_end=excluded.trial_end, current_period_start=excluded.current_period_start,
                 current_period_end=excluded.current_period_end,
                 cancel_at_period_end=excluded.cancel_at_period_end,
                 paused_at=excluded.paused_at, updated_at=excluded.updated_at""",
            (user_id, plan_code, status, provider, provider_reference, trial_end,
             now.isoformat(), period_end, int(cancel_at_period_end), now.isoformat() if paused else None, models._now()),
        )
        conn.commit()
    return get_subscription(user_id)


def change_subscription(user_id, action):
    current = get_subscription(user_id)
    if action == "cancel":
        return set_subscription(user_id, current["plan_code"], current["status"], current.get("provider") or "manual",
                                current.get("provider_reference"), current.get("trial_end"),
                                current.get("current_period_end"), cancel_at_period_end=True)
    if action == "pause":
        return set_subscription(user_id, current["plan_code"], "paused", current.get("provider") or "manual",
                                current.get("provider_reference"), current.get("trial_end"),
                                current.get("current_period_end"), paused=True)
    if action == "resume":
        return set_subscription(user_id, current["plan_code"], "active", current.get("provider") or "manual",
                                current.get("provider_reference"), current.get("trial_end"),
                                current.get("current_period_end"))
    raise ValueError("Aksi subscription tidak dikenal.")


_rate_lock = threading.Lock()
_rate_buckets = {}


def check_rate_limit(key, limit, window_seconds=60):
    """Process-local limiter; use one web process or replace with Redis when scaling."""
    now = time.monotonic()
    with _rate_lock:
        recent = [stamp for stamp in _rate_buckets.get(key, []) if now - stamp < window_seconds]
        if len(recent) >= limit:
            retry_after = max(1, int(window_seconds - (now - recent[0])))
            return False, retry_after
        recent.append(now)
        _rate_buckets[key] = recent
    return True, 0


def public_plans():
    return [{"code": code, **plan} for code, plan in PLANS.items()]
