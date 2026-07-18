"""
runner.py — Subprocess runner with cancellation tracking.

Wraps subprocess.Popen so that long-running external commands (yt-dlp, FFmpeg)
can be terminated when a task is cancelled.
"""

import subprocess
import threading
from typing import Dict, Optional

# task_id -> Popen object for currently running subprocesses
_running_procs: Dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def register_proc(task_id: str, proc: subprocess.Popen) -> None:
    """Register a subprocess under a task id."""
    with _lock:
        _running_procs[task_id] = proc


def unregister_proc(task_id: str) -> None:
    """Unregister a subprocess for a task id."""
    with _lock:
        _running_procs.pop(task_id, None)


def terminate_task(task_id: str) -> bool:
    """
    Terminate all subprocesses registered for a task.
    Returns True if any subprocess was terminated.
    """
    with _lock:
        proc = _running_procs.get(task_id)
    if not proc:
        return False

    try:
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
    except Exception:
        pass
    finally:
        unregister_proc(task_id)
    return True


def is_cancelled(task_id: str) -> bool:
    """Check whether a task has been cancelled (by checking DB status)."""
    import models
    task = models.get_task(task_id)
    return task is not None and task.get("status") == "cancelling"


class TrackedPopen:
    """
    Drop-in replacement for subprocess.Popen that registers the process
    for cancellation tracking. Usage mirrors the standard library.
    """

    def __init__(self, task_id: str, *args, **kwargs):
        self.task_id = task_id
        self._proc = subprocess.Popen(*args, **kwargs)
        register_proc(task_id, self._proc)

    def __enter__(self):
        return self._proc.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return self._proc.__exit__(exc_type, exc_val, exc_tb)
        finally:
            unregister_proc(self.task_id)

    def __getattr__(self, name):
        return getattr(self._proc, name)


def run(task_id: str, cmd, *args, **kwargs):
    """
    Convenience wrapper: run a command with cancellation tracking and return
    the CompletedProcess. The Popen object is unregistered automatically.
    """
    with TrackedPopen(task_id, cmd, *args, **kwargs) as proc:
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )
