"""
queue.py — Bounded task queue with worker pool.

Replaces the per-thread clip model with a fixed pool of workers so that
CPU/memory usage stays bounded when many clips are submitted.
"""

import os
import time
import queue
import threading
import logging
from typing import Optional
from dataclasses import dataclass

import models
import clipper
import runner

logger = logging.getLogger("clipper")


@dataclass
class TaskItem:
    task_id: str
    user_id: Optional[int]
    url: str
    start: str
    end: str
    output_dir: str
    kwargs: dict


class TaskQueue:
    """Bounded queue with a fixed number of worker threads."""

    def __init__(self, max_workers: int = 2, task_timeout: int = 3600):
        self.max_workers = max(1, max_workers)
        self.task_timeout = task_timeout
        self._queue: queue.Queue = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._shutdown = threading.Event()
        self._lock = threading.Lock()
        self._running: dict[str, TaskItem] = {}

    def start(self) -> None:
        """Start the worker threads."""
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"clip-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        logger.info("Task queue started with %d workers", self.max_workers)

    def stop(self, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Signal workers to stop and optionally wait for them."""
        self._shutdown.set()
        # Unblock queue by adding None sentinel for each worker
        for _ in self._workers:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        if wait:
            for t in self._workers:
                t.join(timeout=timeout)

    def submit(
        self,
        task_id: str,
        url: str,
        start: str,
        end: str,
        output_dir: str,
        kwargs: dict,
        user_id: Optional[int] = None,
    ) -> None:
        """Submit a task to the queue. The task must already exist in the DB."""
        item = TaskItem(task_id, user_id, url, start, end, output_dir, kwargs or {})
        self._queue.put(item)
        logger.info("Task %s submitted to queue (user_id=%s)", task_id, user_id)

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a task."""
        task = models.get_task(task_id)
        if not task:
            return False
        if task["status"] in ("done", "error", "cancelled"):
            return False

        models.update_task(task_id, status="cancelling")
        logger.info("Cancellation requested for task %s", task_id)

        # If running, terminate its subprocesses
        terminated = runner.terminate_task(task_id)
        if terminated:
            logger.info("Terminated running subprocesses for task %s", task_id)

        return True

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            item: Optional[TaskItem] = None
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if item is None:
                # shutdown sentinel
                break

            self._run_task(item)
            self._queue.task_done()

    def _run_task(self, item: TaskItem) -> None:
        task_id = item.task_id
        task = models.get_task(task_id)
        if not task or task.get("status") == "cancelling":
            models.update_task(task_id, status="cancelled", progress=0)
            logger.info("Task %s was cancelled before starting", task_id)
            return

        with self._lock:
            self._running[task_id] = item

        models.update_task(task_id, status="queued")
        logger.info("Task %s picked up by worker", task_id)

        # Use a separate thread to enforce timeout
        cancel_timer: Optional[threading.Timer] = None

        def _timeout_cancel():
            logger.warning("Task %s timed out after %d seconds", task_id, self.task_timeout)
            self.cancel(task_id)

        try:
            cancel_timer = threading.Timer(self.task_timeout, _timeout_cancel)
            cancel_timer.start()

            clipper.run_clip(
                task_id=task_id,
                url=item.url,
                start=item.start,
                end=item.end,
                output_dir=item.output_dir,
                **item.kwargs,
            )
        except Exception as exc:
            logger.exception("Task %s failed with error: %s", task_id, exc)
            models.update_task(task_id, status="error", error=str(exc))
        finally:
            if cancel_timer is not None:
                cancel_timer.cancel()
            with self._lock:
                self._running.pop(task_id, None)
            runner.unregister_proc(task_id)

            # If task was cancelled during run, finalize status
            task = models.get_task(task_id)
            if task and task.get("status") == "cancelling":
                models.update_task(task_id, status="cancelled", progress=0)
                logger.info("Task %s finalized as cancelled", task_id)

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()


# Singleton queue instance
_task_queue: Optional[TaskQueue] = None


def get_queue(max_workers: int = 2, task_timeout: int = 3600) -> TaskQueue:
    """Return the singleton task queue, creating it if needed."""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue(max_workers=max_workers, task_timeout=task_timeout)
        _task_queue.start()
    return _task_queue


def submit_task(
    task_id: str,
    url: str,
    start: str,
    end: str,
    output_dir: str,
    kwargs: dict,
    user_id: Optional[int] = None,
) -> None:
    """Create a DB task and submit it to the queue."""
    models.create_task(task_id, user_id=user_id, params={"url": url, "start": start, "end": end, **kwargs})
    get_queue().submit(task_id, url, start, end, output_dir, kwargs, user_id=user_id)


def cancel_task(task_id: str) -> bool:
    """Cancel a queued or running task."""
    return get_queue().cancel(task_id)


def queue_status() -> dict:
    """Return current queue status."""
    q = get_queue()
    return {
        "running": q.running_count,
        "queued": q.queue_size,
        "max_workers": q.max_workers,
    }
