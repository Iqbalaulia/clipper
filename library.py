"""library.py — Projects, custom asset library, presets, and reusable templates.

Per-user organization layer on top of the existing task store:
  * Projects (folders) that group clips.
  * Custom uploaded assets: BGM, B-roll, logo, font — stored encrypted per user.
  * Saved configs: subtitle/format presets and reusable project templates (JSON).

All queries are strictly scoped to user_id to preserve tenant isolation.
"""

import json
import os
import uuid
from typing import Optional

import models
import secure_store

# Asset kinds accepted for the custom asset library.
ASSET_KINDS = {"bgm", "broll", "logo", "font"}
# Saved-config kinds: subtitle/format presets and reusable project templates.
CONFIG_KINDS = {"preset", "template"}

_ASSET_EXT = {
    "bgm": {".mp3", ".wav", ".m4a", ".aac", ".ogg"},
    "broll": {".mp4", ".mov", ".webm", ".mkv"},
    "logo": {".png", ".jpg", ".jpeg", ".webp"},
    "font": {".ttf", ".otf"},
}
MAX_ASSET_BYTES = int(os.environ.get("MAX_ASSET_BYTES", str(50 * 1024 * 1024)))


# ── Projects ─────────────────────────────────────────────────────────────────


def create_project(user_id: int, name: str, description: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Nama project wajib diisi.")
    now = models._now()
    with models._connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (user_id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, name, (description or "").strip(), now, now),
        )
        conn.commit()
        return get_project(user_id, cur.lastrowid)


def get_project(user_id: int, project_id: int) -> Optional[dict]:
    with models._connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def list_projects(user_id: int) -> list:
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_project(user_id: int, project_id: int, name: Optional[str] = None, description: Optional[str] = None) -> Optional[dict]:
    fields = {}
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Nama project tidak boleh kosong.")
        fields["name"] = name
    if description is not None:
        fields["description"] = description.strip()
    if not fields:
        return get_project(user_id, project_id)
    fields["updated_at"] = models._now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with models._connect() as conn:
        cur = conn.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ? AND user_id = ?",
            (*fields.values(), project_id, user_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_project(user_id, project_id)


def delete_project(user_id: int, project_id: int) -> bool:
    """Delete a project. Its tasks are detached (project_id set NULL), not deleted."""
    with models._connect() as conn:
        conn.execute(
            "UPDATE tasks SET project_id = NULL WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )
        cur = conn.execute(
            "DELETE FROM projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def assign_task_to_project(user_id: int, task_id: str, project_id: Optional[int]) -> bool:
    """Move a task into a project (or out, when project_id is None). Both must belong to user."""
    if not models.task_belongs_to_user(task_id, user_id):
        return False
    if project_id is not None and get_project(user_id, project_id) is None:
        raise ValueError("Project tidak ditemukan.")
    with models._connect() as conn:
        conn.execute(
            "UPDATE tasks SET project_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (project_id, models._now(), task_id, user_id),
        )
        conn.commit()
    return True


def list_project_tasks(user_id: int, project_id: int, limit: int = 200) -> list:
    return models.list_tasks(user_id=user_id, project_id=project_id, limit=limit)


# ── Custom asset library ─────────────────────────────────────────────────────


def _asset_dir(user_id: int) -> str:
    path = os.path.join(secure_store.user_private_dir(user_id), "assets")
    os.makedirs(path, exist_ok=True)
    return path


def save_asset(user_id: int, kind: str, name: str, content: bytes, content_type: str = "") -> dict:
    kind = (kind or "").strip().lower()
    if kind not in ASSET_KINDS:
        raise ValueError(f"Jenis asset tidak dikenal: {kind}")
    name = os.path.basename((name or "").strip())
    if not name:
        raise ValueError("Nama file asset wajib diisi.")
    ext = os.path.splitext(name)[1].lower()
    if ext not in _ASSET_EXT[kind]:
        raise ValueError(f"Ekstensi {ext or '(kosong)'} tidak valid untuk {kind}.")
    if not content:
        raise ValueError("File asset kosong.")
    if len(content) > MAX_ASSET_BYTES:
        raise ValueError("Ukuran file melebihi batas maksimum.")

    stored = f"{kind}_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_asset_dir(user_id), stored)
    with open(dest, "wb") as f:
        f.write(content)

    now = models._now()
    with models._connect() as conn:
        cur = conn.execute(
            """INSERT INTO user_assets (user_id, kind, name, stored_filename, content_type, byte_size, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, kind, name, stored, content_type or "", len(content), now),
        )
        conn.commit()
        return get_asset(user_id, cur.lastrowid)


def get_asset(user_id: int, asset_id: int) -> Optional[dict]:
    with models._connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_assets WHERE id = ? AND user_id = ?",
            (asset_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def list_assets(user_id: int, kind: Optional[str] = None) -> list:
    with models._connect() as conn:
        if kind:
            rows = conn.execute(
                "SELECT * FROM user_assets WHERE user_id = ? AND kind = ? ORDER BY created_at DESC",
                (user_id, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_assets WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def asset_path(user_id: int, asset_id: int) -> Optional[str]:
    asset = get_asset(user_id, asset_id)
    if not asset:
        return None
    return os.path.join(_asset_dir(user_id), asset["stored_filename"])


def delete_asset(user_id: int, asset_id: int) -> bool:
    asset = get_asset(user_id, asset_id)
    if not asset:
        return False
    try:
        os.remove(os.path.join(_asset_dir(user_id), asset["stored_filename"]))
    except FileNotFoundError:
        pass
    with models._connect() as conn:
        conn.execute("DELETE FROM user_assets WHERE id = ? AND user_id = ?", (asset_id, user_id))
        conn.commit()
    return True


# ── Saved configs: presets & templates ───────────────────────────────────────


def save_config(user_id: int, kind: str, name: str, data: dict) -> dict:
    kind = (kind or "").strip().lower()
    if kind not in CONFIG_KINDS:
        raise ValueError(f"Jenis konfigurasi tidak dikenal: {kind}")
    name = (name or "").strip()
    if not name:
        raise ValueError("Nama wajib diisi.")
    if not isinstance(data, dict):
        raise TypeError("data harus berupa object.")
    now = models._now()
    with models._connect() as conn:
        conn.execute(
            """INSERT INTO saved_configs (user_id, kind, name, data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, kind, name) DO UPDATE SET
                 data = excluded.data, updated_at = excluded.updated_at""",
            (user_id, kind, name, json.dumps(data), now, now),
        )
        conn.commit()
    return get_config(user_id, kind, name)


def get_config(user_id: int, kind: str, name: str) -> Optional[dict]:
    with models._connect() as conn:
        row = conn.execute(
            "SELECT * FROM saved_configs WHERE user_id = ? AND kind = ? AND name = ?",
            (user_id, kind, name),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["data"] = json.loads(result["data"] or "{}")
    return result


def list_configs(user_id: int, kind: str) -> list:
    with models._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_configs WHERE user_id = ? AND kind = ? ORDER BY updated_at DESC",
            (user_id, kind),
        ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        item["data"] = json.loads(item["data"] or "{}")
        result.append(item)
    return result


def delete_config(user_id: int, config_id: int) -> bool:
    with models._connect() as conn:
        cur = conn.execute(
            "DELETE FROM saved_configs WHERE id = ? AND user_id = ?",
            (config_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
