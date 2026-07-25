"""
Background thread-pool worker.

Runs STEP-extraction tasks off the FastAPI event loop so long-running
CAD operations never block HTTP request handling.

Usage:
    pool = WorkerPool(n_threads=4)
    pool.start()
    pool.submit(job_id, paths, profile, store)
    pool.stop()
"""
import base64
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import List, Optional

from busbarx import extract as _extract
from busbarx import render as _render
from busbarx import bend_profiles

from .job_store import JobStore

logger = logging.getLogger("busbarx.worker")


def _encode_png(png_path: Optional[str]) -> Optional[str]:
    """Read a PNG from disk and return a base-64 string, or None if unavailable."""
    if not png_path or not os.path.exists(png_path):
        return None
    try:
        with open(png_path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except Exception as exc:
        logger.warning("Could not encode PNG %s: %s", png_path, exc)
        return None


def _run_single(step_path: str, profile: dict) -> dict:
    """
    Run extraction for one STEP file in a temp directory.
    Returns a dict: {ok, part, result, png_b64, error}.
    """
    part = os.path.splitext(os.path.basename(step_path))[0]
    with tempfile.TemporaryDirectory(prefix="busbarx_") as tmpdir:
        json_path = os.path.join(tmpdir, part + ".json")
        png_path = os.path.join(tmpdir, part + "_flat.png")
        try:
            prof = bend_profiles.load_profile(
                profile.get("name", "default"),
                path=None
            ) if isinstance(profile, str) else profile

            result = _extract.to_json(step_path, profile=prof)

            import json
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            png_b64 = None
            try:
                _render.render(json_path, png_path)
                png_b64 = _encode_png(png_path)
            except Exception as render_exc:
                logger.warning("Render failed for %s: %s", part, render_exc)

            return {"ok": True, "part": part, "result": result,
                    "visualization_b64": png_b64, "error": None}

        except Exception as exc:
            logger.exception("Extraction failed for %s", step_path)
            return {"ok": False, "part": part, "result": None,
                    "visualization_b64": None, "error": str(exc)}


def _batch_task(job_id: str, step_paths: List[str], profile: dict,
                store: JobStore) -> None:
    """Worker function — runs in the thread pool."""
    store.set_processing(job_id)
    results = []
    for i, path in enumerate(step_paths):
        logger.info("[job=%s] processing %d/%d: %s",
                    job_id, i + 1, len(step_paths), os.path.basename(path))
        res = _run_single(path, profile)
        results.append(res)
        store.update_progress(job_id, done=i + 1)

    store.set_completed(job_id, results)
    ok_count = sum(1 for r in results if r["ok"])
    logger.info("[job=%s] done — %d/%d ok", job_id, ok_count, len(results))


class WorkerPool:
    def __init__(self, n_threads: int = 4):
        self._n = n_threads
        self._executor: Optional[ThreadPoolExecutor] = None

    def start(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=self._n,
            thread_name_prefix="busbarx-worker",
        )
        logger.info("WorkerPool started with %d threads", self._n)

    def stop(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            logger.info("WorkerPool stopped")

    def submit(self, job_id: str, step_paths: List[str],
               profile: dict, store: JobStore) -> Future:
        if self._executor is None:
            raise RuntimeError("WorkerPool not started")
        future = self._executor.submit(
            _batch_task, job_id, step_paths, profile, store
        )
        future.add_done_callback(lambda f: _on_future_done(f, job_id, store))
        return future


def _on_future_done(future: Future, job_id: str, store: JobStore) -> None:
    """Callback — catches any unhandled exception and marks job failed."""
    exc = future.exception()
    if exc:
        logger.error("[job=%s] unhandled worker exception: %s", job_id, exc)
        store.set_failed(job_id, str(exc))
