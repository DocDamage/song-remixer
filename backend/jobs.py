from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import threading
import time
from typing import Any


AUTO_MIX_JOB_KIND = "auto-mix"
STEM_SPLIT_JOB_KIND = "split-stems"
JOB_HISTORY_LIMIT = 8


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InProcessJobStore:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=JOB_HISTORY_LIMIT)

    def create(self, job_id: str, kind: str, input_files: dict[str, str]) -> dict[str, Any]:
        job = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "progress": 0,
            "message": "Queued and waiting to start.",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "input_files": dict(input_files),
            "result": None,
            "error": None,
            "expires_at": None,
            "status_url": f"/jobs/{job_id}",
            "history_url": "/history",
        }
        with self.lock:
            self.jobs[job_id] = job
        return self.snapshot(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return dict(job)

    def update(self, job_id: str, **changes: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if "progress" in changes and changes["progress"] is not None:
                changes["progress"] = max(job.get("progress", 0), changes["progress"])
            job.update(changes)
            job["updated_at"] = utc_now_iso()

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "completed"
            job["progress"] = 100
            job["message"] = result.get("status_line") or "Job completed."
            job["result"] = result
            job["updated_at"] = utc_now_iso()
            job["expires_at"] = time.time() + self.ttl_seconds
            self.history.appendleft(dict(job))

    def fail(self, job_id: str, error_message: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "failed"
            job["progress"] = min(job.get("progress", 0), 99)
            job["message"] = error_message
            job["error"] = error_message
            job["updated_at"] = utc_now_iso()
            job["expires_at"] = time.time() + self.ttl_seconds
            self.history.appendleft(dict(job))

    def list_history(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(entry) for entry in self.history]
