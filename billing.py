"""Midtrans Snap checkout, invoice persistence, and webhook processing."""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import requests

import models
import saas


def configured():
    return bool(os.environ.get("MIDTRANS_SERVER_KEY"))


def _base_url():
    return "https://app.midtrans.com" if os.environ.get("MIDTRANS_IS_PRODUCTION", "false").lower() == "true" else "https://app.sandbox.midtrans.com"


def create_checkout(user, plan_code):
    if plan_code not in saas.PLANS or plan_code == "free":
        raise ValueError("Plan berbayar tidak valid.")
    if not configured():
        raise RuntimeError("MIDTRANS_SERVER_KEY belum dikonfigurasi.")
    plan = saas.PLANS[plan_code]
    invoice_id = f"INV-{uuid.uuid4().hex[:20].upper()}"
    payload = {
        "transaction_details": {"order_id": invoice_id, "gross_amount": plan["price"]},
        "customer_details": {"first_name": user.name or "Clipper User", "email": user.email},
        "item_details": [{"id": plan_code, "price": plan["price"], "quantity": 1, "name": f"Clipper {plan['name']} - 1 bulan"}],
        "callbacks": {"finish": os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000") + "/?checkout=success"},
    }
    response = requests.post(
        _base_url() + "/snap/v1/transactions", json=payload,
        auth=(os.environ["MIDTRANS_SERVER_KEY"], ""), timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    with models._connect() as conn:
        conn.execute(
            """INSERT INTO invoices
               (id, user_id, plan_code, amount, currency, status, provider_reference, checkout_url, created_at)
               VALUES (?, ?, ?, ?, 'IDR', 'pending', ?, ?, ?)""",
            (invoice_id, user.id, plan_code, plan["price"], result.get("token"), result.get("redirect_url"), models._now()),
        )
        conn.commit()
    return {"invoice_id": invoice_id, "checkout_url": result["redirect_url"], "token": result.get("token")}


def _valid_signature(payload):
    value = payload.get("order_id", "") + payload.get("status_code", "") + payload.get("gross_amount", "") + os.environ.get("MIDTRANS_SERVER_KEY", "")
    expected = hashlib.sha512(value.encode("utf-8")).hexdigest()
    return bool(payload.get("signature_key")) and expected == payload["signature_key"]


def process_webhook(payload):
    if not _valid_signature(payload):
        raise ValueError("Signature Midtrans tidak valid.")
    event_id = f"{payload.get('order_id')}:{payload.get('transaction_status')}:{payload.get('status_code')}"
    with models._connect() as conn:
        try:
            conn.execute(
                "INSERT INTO payment_events (provider, event_id, payload, processed_at) VALUES ('midtrans', ?, ?, ?)",
                (event_id, json.dumps(payload), models._now()),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return {"duplicate": True}
        invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (payload.get("order_id"),)).fetchone()
        if not invoice:
            conn.rollback()
            raise ValueError("Invoice tidak ditemukan.")
        status = payload.get("transaction_status")
        paid = status in ("capture", "settlement") and payload.get("fraud_status", "accept") == "accept"
        invoice_status = "paid" if paid else ("failed" if status in ("deny", "cancel", "expire", "failure") else "pending")
        conn.execute(
            "UPDATE invoices SET status = ?, paid_at = ? WHERE id = ?",
            (invoice_status, models._now() if paid else None, invoice["id"]),
        )
        conn.commit()
    if paid:
        plan = saas.PLANS[invoice["plan_code"]]
        trial_end = (datetime.now(timezone.utc) + timedelta(days=plan["trial_days"])).isoformat() if plan["trial_days"] else None
        saas.set_subscription(
            invoice["user_id"], invoice["plan_code"], "trialing" if trial_end else "active",
            "midtrans", payload.get("transaction_id"), trial_end=trial_end,
        )
    return {"duplicate": False, "status": invoice_status}


def list_invoices(user_id):
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id, plan_code, amount, currency, status, checkout_url, created_at, paid_at FROM invoices WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]
