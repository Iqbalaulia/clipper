"""apikeys.py — Per-user REST API keys for programmatic access.

Each key has the form `ck_live_<43 chars>` and is stored as a sha256 hash so
the plaintext is only visible once at creation time. A short prefix is kept
in the DB so users can identify which key is which in the management UI.

Keys authenticate via the `Authorization: Bearer <key>` or `X-API-Key`
header and are scoped to the owning user with their own rate-limit bucket.
"""

import hashlib
import os
import secrets
from typing import Optional

import models

_KEY_PREFIX = "ck_live_"
# Rate limit applied to every API-key-authenticated request (per key).
API_KEY_RATE_LIMIT = int(os.environ.get("API_KEY_RATE_LIMIT", "60"))
API_KEY_RATE_WINDOW = int(os.environ.get("API_KEY_RATE_WINDOW", "60"))


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_key() -> str:
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def create_api_key(user_id: int, name: str) -> dict:
    """Create a new API key. The plaintext key is returned only here."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Nama API key wajib diisi.")
    plaintext = _generate_key()
    now = models._now()
    with models._connect() as conn:
        cur = conn.execute(
            """INSERT INTO api_keys (user_id, name, prefix, key_hash, is_active, created_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (user_id, name, plaintext[:14], _hash_key(plaintext), now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": name, "prefix": plaintext[:14], "key": plaintext}


def list_api_keys(user_id: int) -> list:
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT id, name, prefix, is_active, last_used_at, created_at FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["is_active"] = bool(item["is_active"])
            result.append(item)
        return result


def verify_api_key(plaintext: str) -> Optional[int]:
    """Return the user_id for a valid, active API key, else None."""
    if not plaintext or not plaintext.startswith(_KEY_PREFIX):
        return None
    key_hash = _hash_key(plaintext.strip())
    with models._connect() as conn:
        row = conn.execute(
            "SELECT id, user_id, is_active FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if not row or not row["is_active"]:
            return None
        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (models._now(), row["id"]),
        )
        conn.commit()
        return row["user_id"]


def revoke_api_key(user_id: int, key_id: int) -> bool:
    with models._connect() as conn:
        cur = conn.execute(
            "DELETE FROM api_keys WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def set_api_key_active(user_id: int, key_id: int, is_active: bool) -> bool:
    with models._connect() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET is_active = ? WHERE id = ? AND user_id = ?",
            (int(bool(is_active)), key_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
