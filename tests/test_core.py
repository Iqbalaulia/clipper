"""
tests/test_core.py — Smoke tests for the refactored Clipper backend.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models
import task_queue
import clipper


def test_create_and_get_task():
    task_id = "test-task-create"
    models.create_task(task_id, {"url": "https://example.com/video", "start": "0", "end": "10"})
    task = models.get_task(task_id)
    assert task is not None
    assert task["status"] == "pending"
    assert task["progress"] == 0
    assert task["params"]["url"] == "https://example.com/video"
    models.delete_task(task_id)


def test_update_task_and_logs():
    task_id = "test-task-logs-2"
    models.delete_task(task_id)
    models.create_task(task_id, {})
    models.update_task(task_id, status="downloading", progress=25)
    models.append_log(task_id, "downloading video")
    models.append_log(task_id, "done")
    task = models.get_task(task_id)
    assert task["status"] == "downloading"
    assert task["progress"] == 25
    assert len(task["logs"]) == 2
    models.delete_task(task_id)


def test_clipper_state_compat():
    task_id = clipper.create_task()
    assert task_id
    task = clipper.get_task(task_id)
    assert task["status"] == "pending"
    clipper._update_task(task_id, status="done", progress=100)
    clipper._append_log(task_id, "completed")
    task = clipper.get_task(task_id)
    assert task["status"] == "done"
    assert len(task["logs"]) == 1


def test_queue_status():
    q = task_queue.get_queue(max_workers=2)
    status = task_queue.queue_status()
    assert status["max_workers"] == 2
    assert status["queued"] >= 0
    assert status["running"] >= 0


def test_parse_seconds_helpers():
    assert clipper._parse_seconds("90") == 90.0
    assert clipper._parse_seconds("01:30") == 90.0
    assert clipper._parse_seconds("00:01:30") == 90.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
