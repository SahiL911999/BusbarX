"""
Thread-safe in-memory job store.

Interface is intentionally thin so it can be swapped for a Redis-backed
implementation later with zero changes to the routers.

Job record shape:
{
    "id":        str (uuid4),
    "status":    "queued" | "processing" | "completed" | "failed",
    "file_count": int,
    "progress":  {"done": int, "total": int},
    "results":   list | None,
    "error":     str | None,
    "created_at": float (time.time()),
    "elapsed_s": float | None,
}
"""
import threading
import time
import uuid
from typing import Any, Dict, Optional


class JobStore:
    def __init__(self, ttl_seconds: int = 3600):
        self._lock = threading.RLock()
        self._store: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl_seconds

    # ── write ────────────────────────────────────────────────────────────────

    def create(self, file_count: int) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._store[job_id] = {
                "id": job_id,
                "status": "queued",
                "file_count": file_count,
                "progress": {"done": 0, "total": file_count},
                "results": None,
                "error": None,
                "created_at": time.time(),
                "elapsed_s": None,
            }
        return job_id

    def set_processing(self, job_id: str) -> None:
        with self._lock:
            job = self._store.get(job_id)
            if job:
                job["status"] = "processing"
                job["_started_at"] = time.time()

    def update_progress(self, job_id: str, done: int) -> None:
        with self._lock:
            job = self._store.get(job_id)
            if job:
                job["progress"]["done"] = done

    def set_completed(self, job_id: str, results: list) -> None:
        with self._lock:
            job = self._store.get(job_id)
            if job:
                job["status"] = "completed"
                job["results"] = results
                started = job.get("_started_at", job["created_at"])
                job["elapsed_s"] = round(time.time() - started, 3)

    def set_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._store.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = error
                started = job.get("_started_at", job["created_at"])
                job["elapsed_s"] = round(time.time() - started, 3)

    # ── read ─────────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._store.get(job_id)

    def exists(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._store

    # ── delete ────────────────────────────────────────────────────────────────

    def delete(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._store:
                del self._store[job_id]
                return True
            return False

    # ── TTL eviction (call periodically or on-demand) ─────────────────────────

    def evict_expired(self) -> int:
        now = time.time()
        expired = []
        with self._lock:
            for jid, job in self._store.items():
                if now - job["created_at"] > self._ttl:
                    expired.append(jid)
            for jid in expired:
                del self._store[jid]
        return len(expired)


# ── module-level singleton (shared across all requests) ───────────────────────
_store: Optional[JobStore] = None


def get_store() -> JobStore:
    global _store
    if _store is None:
        _store = JobStore()
    return _store
