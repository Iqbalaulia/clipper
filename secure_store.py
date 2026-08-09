"""Encrypted per-user secret storage and temporary cookie materialization."""

import base64
import hashlib
import os
import tempfile
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KEY_FILE = os.path.join(DATA_DIR, ".encryption_key")


def _load_key() -> bytes:
    configured = os.environ.get("DATA_ENCRYPTION_KEY", "").strip()
    if configured:
        try:
            Fernet(configured.encode("ascii"))
            return configured.encode("ascii")
        except (ValueError, TypeError):
            return base64.urlsafe_b64encode(hashlib.sha256(configured.encode("utf-8")).digest())

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.isfile(KEY_FILE):
        with open(KEY_FILE, "rb") as key_file:
            return key_file.read().strip()

    key = Fernet.generate_key()
    try:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(key)
        return key
    except FileExistsError:
        with open(KEY_FILE, "rb") as key_file:
            return key_file.read().strip()


_FERNET = Fernet(_load_key())


def encrypt_bytes(value: bytes) -> bytes:
    return _FERNET.encrypt(value)


def decrypt_bytes(value: bytes) -> bytes:
    try:
        return _FERNET.decrypt(value)
    except InvalidToken as exc:
        raise ValueError("Data terenkripsi tidak dapat dibuka dengan encryption key saat ini.") from exc


def encrypt_text(value: str) -> str:
    return encrypt_bytes(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    return decrypt_bytes(value.encode("ascii")).decode("utf-8")


def user_private_dir(user_id: int) -> str:
    path = os.path.join(DATA_DIR, "users", str(int(user_id)))
    os.makedirs(path, exist_ok=True)
    return path


def user_cookies_path(user_id: int) -> str:
    return os.path.join(user_private_dir(user_id), "cookies.enc")


def save_user_cookies(user_id: int, content: bytes) -> None:
    destination = user_cookies_path(user_id)
    temporary = destination + ".tmp"
    with open(temporary, "wb") as cookie_file:
        cookie_file.write(encrypt_bytes(content))
    os.replace(temporary, destination)


def has_user_cookies(user_id: int) -> bool:
    return os.path.isfile(user_cookies_path(user_id))


@contextmanager
def materialize_user_cookies(user_id: int):
    """Decrypt cookies to a short-lived file accepted by yt-dlp."""
    encrypted_path = user_cookies_path(user_id)
    if not os.path.isfile(encrypted_path):
        yield ""
        return

    with open(encrypted_path, "rb") as cookie_file:
        content = decrypt_bytes(cookie_file.read())

    fd, path = tempfile.mkstemp(prefix=f"clipper_cookies_{user_id}_", suffix=".txt")
    try:
        with os.fdopen(fd, "wb") as temporary:
            temporary.write(content)
        yield path
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
