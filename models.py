"""
models.py — Persistent task state using SQLite.

This module replaces the in-memory _tasks dictionary with an SQLite-backed
store so that task progress survives server restarts and can be inspected
after the fact.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            output_file TEXT,
            error TEXT,
            params TEXT NOT NULL DEFAULT '{}',
            logs TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at);

        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_task_logs_task_id ON task_logs(task_id);
        """
    )
    conn.commit()


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


def create_task(task_id: str, params: Optional[dict] = None) -> dict:
    """Create a new task row and return its state."""
    params = params or {}
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, status, progress, output_file, error, params, logs, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, "pending", 0, None, None, json.dumps(params), "[]", now, now),
        )
        conn.commit()
    return get_task(task_id)


def get_task(task_id: str) -> Optional[dict]:
    """Return a task dict including its logs."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, status, progress, output_file, error, params, created_at, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
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
    allowed = {"status", "progress", "output_file", "error"}
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


def list_tasks(status: Optional[str] = None, limit: int = 100) -> list:
    """List tasks, optionally filtered by status."""
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT id FROM tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [get_task(r["id"]) for r in rows]


def delete_task(task_id: str) -> bool:
    """Delete a task and its logs."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
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


# Initialize tables on import
init_db()
