"""
models.py — Persistent task state using SQLite.

This module replaces the in-memory _tasks dictionary with an SQLite-backed
store so that task progress survives server restarts and can be inspected
after the fact.
"""

import os
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager

from werkzeug.security import check_password_hash
from flask_login import UserMixin

try:
    import bcrypt
except Exception:  # pragma: no cover
    bcrypt = None


def _hash_password(password: str) -> str:
    """Hash a password with bcrypt when available, fallback to Werkzeug."""
    if bcrypt is not None:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    # Fallback for environments without bcrypt (not recommended for production).
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt or legacy Werkzeug hash."""
    if not password_hash:
        return False
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        if bcrypt is None:
            return False
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    return check_password_hash(password_hash, password)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "clipper.db")

os.makedirs(DATA_DIR, exist_ok=True)


@dataclass
class Task:
    id: str
    status: str
    progress: int
    output_file: Optional[str]
    error: Optional[str]
    params: dict
    created_at: str
    updated_at: str
    virality_score: Optional[int] = None
    virality_reason: Optional[str] = None
    thumbnail_file: Optional[str] = None
    moment_index: int = 0


class User(UserMixin):
    """Flask-Login compatible user model backed by SQLite."""

    def __init__(
        self,
        id: int,
        email: str,
        name: str,
        is_active: bool = True,
        email_verified: bool = False,
        avatar_url: str = "",
        timezone: str = "",
        language: str = "",
    ):
        self.id = id
        self.email = email
        self.name = name
        self._is_active = is_active
        self.email_verified = email_verified
        self.avatar_url = avatar_url or ""
        self.timezone = timezone or "UTC"
        self.language = language or "id"

    @property
    def is_active(self) -> bool:
        return self._is_active

    @staticmethod
    def from_row(row: sqlite3.Row) -> "User":
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"] or "",
            is_active=bool(row["is_active"]),
            email_verified=bool(row["email_verified"]),
            avatar_url=row["avatar_url"] or "",
            timezone=row["timezone"] or "UTC",
            language=row["language"] or "id",
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables(conn: sqlite3.Connection) -> None:
    # 1. Create tables first (without indexes that may reference missing columns).
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            email_verified INTEGER NOT NULL DEFAULT 0,
            email_verification_token TEXT,
            email_verification_expires_at TEXT,
            password_reset_token TEXT,
            password_reset_expires_at TEXT,
            avatar_url TEXT,
            timezone TEXT,
            language TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            output_file TEXT,
            error TEXT,
            params TEXT NOT NULL DEFAULT '{}',
            logs TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    # 2. Backward-compatible migrations: add columns introduced by later features.
    _add_column_if_missing(conn, "tasks", "user_id", "INTEGER")
    _add_column_if_missing(conn, "tasks", "virality_score", "INTEGER")
    _add_column_if_missing(conn, "tasks", "virality_reason", "TEXT")
    _add_column_if_missing(conn, "tasks", "thumbnail_file", "TEXT")
    _add_column_if_missing(conn, "tasks", "moment_index", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "users", "email_verified", "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "users", "email_verification_token", "TEXT")
    _add_column_if_missing(conn, "users", "email_verification_expires_at", "TEXT")
    _add_column_if_missing(conn, "users", "password_reset_token", "TEXT")
    _add_column_if_missing(conn, "users", "password_reset_expires_at", "TEXT")
    _add_column_if_missing(conn, "users", "avatar_url", "TEXT")
    _add_column_if_missing(conn, "users", "timezone", "TEXT")
    _add_column_if_missing(conn, "users", "language", "TEXT")
    # 3. Create indexes after columns are guaranteed to exist.
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at);
        CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_logs(task_id);
        """
    )
    conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    """Add a column only if it does not already exist (SQLite-safe)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _init_tables(conn)
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database tables."""
    with _connect() as conn:
        _init_tables(conn)


def create_task(
    task_id: str,
    user_id: Optional[int] = None,
    params: Optional[dict] = None,
    virality_score: Optional[int] = None,
    virality_reason: Optional[str] = None,
    thumbnail_file: Optional[str] = None,
    moment_index: int = 0,
) -> dict:
    """Create a new task row and return its state."""
    params = params or {}
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (
                id, user_id, status, progress, output_file, error, params, logs,
                created_at, updated_at, virality_score, virality_reason, thumbnail_file, moment_index
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id, user_id, "pending", 0, None, None, json.dumps(params), "[]",
                now, now, virality_score, virality_reason, thumbnail_file, moment_index,
            ),
        )
        conn.commit()
    return get_task(task_id)


def get_task(task_id: str, user_id: Optional[int] = None) -> Optional[dict]:
    """Return a task dict including its logs. If user_id is provided, only return
    tasks owned by that user (or orphan tasks with no owner for backward compat)."""
    with _connect() as conn:
        sql = """
            SELECT
                id, user_id, status, progress, output_file, error, params, created_at, updated_at,
                virality_score, virality_reason, thumbnail_file, moment_index
            FROM tasks WHERE id = ?
        """
        params = [task_id]
        if user_id is not None:
            sql += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None

        logs = [
            r["message"]
            for r in conn.execute(
                "SELECT message FROM task_logs WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            )
        ]
        task = dict(row)
        task["params"] = json.loads(task["params"] or "{}")
        task["logs"] = logs
        return task


def update_task(task_id: str, **kwargs) -> bool:
    """Update one or more task fields."""
    allowed = {
        "status", "progress", "output_file", "error",
        "virality_score", "virality_reason", "thumbnail_file", "moment_index",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]

    with _connect() as conn:
        cur = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return cur.rowcount > 0


def append_log(task_id: str, message: str) -> None:
    """Append a log message to a task."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO task_logs (task_id, message, created_at) VALUES (?, ?, ?)",
            (task_id, message, now),
        )
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        conn.commit()


def list_tasks(status: Optional[str] = None, user_id: Optional[int] = None, limit: int = 100) -> list:
    """List tasks, optionally filtered by status and/or user."""
    with _connect() as conn:
        where_clauses = []
        params = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if user_id is not None:
            where_clauses.append("(user_id = ? OR user_id IS NULL)")
            params.append(user_id)

        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        sql = f"SELECT id FROM tasks {where} ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [get_task(r["id"], user_id=user_id) for r in rows]


def delete_task(task_id: str, user_id: Optional[int] = None) -> bool:
    """Delete a task and its logs. If user_id is provided, only delete the user's task."""
    with _connect() as conn:
        sql = "DELETE FROM tasks WHERE id = ?"
        params = [task_id]
        if user_id is not None:
            sql += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0


def get_running_task_count() -> int:
    """Return number of tasks currently running."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('downloading', 'subtitles', 'tracking', 'processing', 'cutting')"
        ).fetchone()
        return row[0] if row else 0


def get_pending_tasks(limit: int = 10) -> list:
    """Return pending tasks ordered by creation time."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [get_task(r["id"]) for r in rows]


def cleanup_old_tasks(days: int = 7) -> int:
    """Delete tasks older than N days that are done or errored."""
    cutoff = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            DELETE FROM tasks
            WHERE status IN ('done', 'error', 'cancelled')
              AND updated_at < datetime(?, '-{} days')
            """.format(days),
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount


def reset_stale_tasks() -> int:
    """Mark running tasks as error on server startup (they were killed)."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE tasks
            SET status = 'error', error = 'Server restarted while task was running', updated_at = ?
            WHERE status IN ('downloading', 'subtitles', 'tracking', 'processing', 'cutting')
            """,
            (_now(),),
        )
        conn.commit()
        return cur.rowcount


def set_setting(key: str, value: str) -> None:
    """Store or update a setting value."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    """Return a setting value or default if not found."""
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


# ── Auth helpers ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def create_user(email: str, password: str, name: str = "") -> Optional[User]:
    """Create a new user. Returns the User on success, None on duplicate email."""
    email = email.strip().lower()
    name = (name or "").strip()
    if not is_valid_email(email):
        raise ValueError("Format email tidak valid.")
    if len(password) < 8:
        raise ValueError("Password minimal 8 karakter.")

    password_hash = _hash_password(password)
    now = _now()
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO users (email, password_hash, name, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (email, password_hash, name, 1, now, now),
            )
            conn.commit()
            return get_user_by_id(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None


def get_user_by_id(user_id: int) -> Optional[User]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, name, is_active, email_verified, avatar_url, timezone, language FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return User.from_row(row) if row else None


def get_user_by_email(email: str) -> Optional[User]:
    email = email.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, name, is_active, email_verified, avatar_url, timezone, language FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        return User.from_row(row) if row else None


def authenticate_user(email: str, password: str) -> Optional[User]:
    """Verify email/password and return User if valid."""
    user_row = get_user_by_email(email)
    if not user_row:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (user_row.id,),
        ).fetchone()
    if not row:
        return None
    if _verify_password(password, row["password_hash"]):
        return user_row
    return None


def update_user_profile(user_id: int, name: str = "", avatar_url: str = "", timezone: str = "", language: str = "") -> Optional[User]:
    """Update a user's profile fields."""
    name = (name or "").strip()
    avatar_url = (avatar_url or "").strip()
    timezone = (timezone or "UTC").strip()
    language = (language or "id").strip()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET name = ?, avatar_url = ?, timezone = ?, language = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, avatar_url, timezone, language, _now(), user_id),
        )
        conn.commit()
    return get_user_by_id(user_id)


def set_email_verification_token(user_id: int, token: str, expires_at: str) -> None:
    """Store an email verification token for the user."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET email_verification_token = ?, email_verification_expires_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (token, expires_at, _now(), user_id),
        )
        conn.commit()


def get_user_by_email_verification_token(token: str) -> Optional[User]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, email, password_hash, name, is_active, email_verified, avatar_url, timezone, language
            FROM users
            WHERE email_verification_token = ? AND email_verification_expires_at > ?
            """,
            (token, _now()),
        ).fetchone()
        return User.from_row(row) if row else None


def verify_email(token: str) -> bool:
    """Mark a user's email as verified using a valid token."""
    user = get_user_by_email_verification_token(token)
    if not user:
        return False
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET email_verified = 1, email_verification_token = NULL,
                email_verification_expires_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_now(), user.id),
        )
        conn.commit()
    return True


def set_password_reset_token(user_id: int, token: str, expires_at: str) -> None:
    """Store a password reset token for the user."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET password_reset_token = ?, password_reset_expires_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (token, expires_at, _now(), user_id),
        )
        conn.commit()


def get_user_by_password_reset_token(token: str) -> Optional[User]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, email, password_hash, name, is_active, email_verified, avatar_url, timezone, language
            FROM users
            WHERE password_reset_token = ? AND password_reset_expires_at > ?
            """,
            (token, _now()),
        ).fetchone()
        return User.from_row(row) if row else None


def reset_password(token: str, new_password: str) -> bool:
    """Reset a user's password using a valid token."""
    if len(new_password) < 8:
        raise ValueError("Password minimal 8 karakter.")
    user = get_user_by_password_reset_token(token)
    if not user:
        return False
    password_hash = _hash_password(new_password)
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, password_reset_token = NULL,
                password_reset_expires_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (password_hash, _now(), user.id),
        )
        conn.commit()
    return True


def task_belongs_to_user(task_id: str, user_id: int) -> bool:
    """Check whether a task is owned by the given user (or is an orphan task)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return False
        return row["user_id"] is None or row["user_id"] == user_id


def get_task_by_output_file(filename: str, user_id: Optional[int] = None) -> Optional[dict]:
    """Find a task by its output filename, optionally scoped to a user."""
    with _connect() as conn:
        sql = "SELECT id FROM tasks WHERE output_file = ?"
        params = [filename]
        if user_id is not None:
            sql += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        row = conn.execute(sql, params).fetchone()
        return get_task(row["id"], user_id=user_id) if row else None


def get_task_by_thumbnail_file(filename: str, user_id: Optional[int] = None) -> Optional[dict]:
    """Find a task by its thumbnail filename, optionally scoped to a user."""
    with _connect() as conn:
        sql = "SELECT id FROM tasks WHERE thumbnail_file = ?"
        params = [filename]
        if user_id is not None:
            sql += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        row = conn.execute(sql, params).fetchone()
        return get_task(row["id"], user_id=user_id) if row else None


# Initialize tables on import
init_db()
