"""S3-compatible asset persistence for AWS S3, R2, Wasabi, and MinIO."""

import mimetypes
import os

import models


def is_configured():
    return bool(os.environ.get("S3_BUCKET") and os.environ.get("S3_ACCESS_KEY_ID") and os.environ.get("S3_SECRET_ACCESS_KEY"))


def _client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
        region_name=os.environ.get("S3_REGION", "auto"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
    )


def _upsert_asset(task_id, user_id, kind, provider, object_key, local_filename, content_type, byte_size, status="ready"):
    now = models._now()
    with models._connect() as conn:
        conn.execute(
            """INSERT INTO assets
               (task_id, user_id, kind, provider, object_key, local_filename,
                content_type, byte_size, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id, kind) DO UPDATE SET provider=excluded.provider,
                 object_key=excluded.object_key, local_filename=excluded.local_filename,
                 content_type=excluded.content_type, byte_size=excluded.byte_size,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (task_id, user_id, kind, provider, object_key, local_filename,
             content_type, byte_size, status, now, now),
        )
        conn.commit()


def persist_task_assets(task_id, output_path, thumbnail_path=None):
    """Persist final assets and return their metadata. Upload is idempotent by object key."""
    task = models.get_task(task_id)
    if not task or task.get("user_id") is None:
        raise ValueError("Task tidak memiliki pemilik untuk penyimpanan asset.")
    user_id = task["user_id"]
    entries = [("clip", output_path)]
    if thumbnail_path and os.path.isfile(thumbnail_path):
        entries.append(("thumbnail", thumbnail_path))

    cloud = is_configured()
    client = _client() if cloud else None
    bucket = os.environ.get("S3_BUCKET", "")
    provider = "s3" if cloud else "local"
    results = {}
    for kind, path in entries:
        if not path or not os.path.isfile(path):
            continue
        filename = os.path.basename(path)
        extension = os.path.splitext(filename)[1]
        object_key = f"users/{user_id}/tasks/{task_id}/{kind}{extension}"
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        if cloud:
            client.upload_file(path, bucket, object_key, ExtraArgs={"ContentType": content_type})
        _upsert_asset(task_id, user_id, kind, provider, object_key, filename, content_type, size)
        results[kind] = {"object_key": object_key, "provider": provider, "filename": filename}

    if cloud and results.get("clip"):
        for _, path in entries:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
    return results


def get_asset(task_id, user_id, kind):
    with models._connect() as conn:
        row = conn.execute(
            "SELECT * FROM assets WHERE task_id = ? AND user_id = ? AND kind = ? AND status = 'ready'",
            (task_id, user_id, kind),
        ).fetchone()
        return dict(row) if row else None


def get_asset_by_filename(filename, user_id, kind):
    with models._connect() as conn:
        row = conn.execute(
            """SELECT * FROM assets WHERE local_filename = ? AND user_id = ?
               AND kind = ? AND status = 'ready'""",
            (filename, user_id, kind),
        ).fetchone()
        return dict(row) if row else None


def signed_url(asset, download=False, expires=None):
    if not asset or asset.get("provider") != "s3":
        return ""
    expires = expires or int(os.environ.get("SIGNED_URL_EXPIRES", "900"))
    params = {"Bucket": os.environ["S3_BUCKET"], "Key": asset["object_key"]}
    if download:
        params["ResponseContentDisposition"] = f'attachment; filename="{asset["local_filename"]}"'
    return _client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def asset_urls(task_id, user_id):
    result = {}
    for kind in ("clip", "thumbnail"):
        asset = get_asset(task_id, user_id, kind)
        if not asset:
            continue
        if asset["provider"] == "s3":
            result[f"{kind}_url"] = signed_url(asset, download=False)
            if kind == "clip":
                result["download_url"] = signed_url(asset, download=True)
        else:
            route = "download" if kind == "clip" else "download-thumb"
            result[f"{kind}_url"] = f"/{route}/{asset['local_filename']}"
            if kind == "clip":
                result["download_url"] = result["clip_url"]
    return result
