"""
Extract router — /v1/extract/single and /v1/extract/batch

/v1/extract/single  POST  — synchronous; runs inline with a timeout guard
/v1/extract/batch   POST  — async; submits to thread pool, returns 202 + job_id
/v1/profiles        GET   — list built-in bend profiles
/v1/profiles/validate POST — validate a custom profile JSON without running extraction
"""
import asyncio
import base64
import json
import logging
import os
import tempfile
import time
import uuid
from typing import List, Optional, Set

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from busbarx import bend_profiles
from busbarx import extract as _extract
from busbarx import render as _render

from ..models.responses import (
    BatchSubmitResponse,
    JobStatus,
    ProfileInfo,
    ProfilesResponse,
    ProfileValidateResponse,
    SingleExtractionResponse,
    StepV2Result,
)
from ..services.job_store import get_store

logger = logging.getLogger("busbarx.router.extract")

router = APIRouter(tags=["Extraction"])

MAX_FILES = 10
ALLOWED_EXTENSIONS = {".stp", ".step"}
MAX_FILE_SIZE_MB = 50
SINGLE_TIMEOUT_S = int(os.getenv("SINGLE_TIMEOUT_S", "120"))


# ── helpers ───────────────────────────────────────────────────────────────────

def _validate_extension(filename: str) -> None:
    ext = os.path.splitext(filename)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type {ext!r}. Only .stp / .step files are accepted.",
        )


def _resolve_profile(profile_name: str, profile_json: Optional[str]) -> dict:
    """Resolve bend profile from name or inline JSON string."""
    if profile_json:
        try:
            raw = json.loads(profile_json)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"profile_json is not valid JSON: {e}",
            )
        try:
            bend_profiles._validate(raw, "<inline profile_json>")
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            )
        raw.setdefault("name", "inline")
        return raw
    try:
        return bend_profiles.load_profile(profile_name)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


def _safe_filename(filename: Optional[str], used: Optional[Set[str]] = None) -> str:
    """Sanitize a client-supplied filename to a bare base name — strips any
    directory-traversal / path components (e.g. '../../etc/x.stp' -> 'x.stp') so
    it can never be joined outside its intended destination directory. If `used`
    is given, disambiguates collisions within one request (two uploads with the
    same name would otherwise silently overwrite each other before processing).
    """
    name = os.path.basename((filename or "").replace("\\", "/").strip())
    if not name or name in (".", ".."):
        name = f"{uuid.uuid4()}.stp"
    if used is not None:
        base, ext = os.path.splitext(name)
        candidate, n = name, 1
        while candidate in used:
            n += 1
            candidate = f"{base}_{n}{ext}"
        used.add(candidate)
        name = candidate
    return name


async def _save_upload(upload: UploadFile, dest_dir: str,
                        used: Optional[Set[str]] = None) -> str:
    """Save an uploaded file to dest_dir and return its path. The filename is
    sanitized so it can never escape dest_dir (path traversal) or collide with
    another file already saved in the same request."""
    _validate_extension(upload.filename or "file.stp")
    dest = os.path.join(dest_dir, _safe_filename(upload.filename, used))
    content = await upload.read()
    if len(content) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File {upload.filename!r} exceeds the {MAX_FILE_SIZE_MB} MB limit.",
        )
    with open(dest, "wb") as fh:
        fh.write(content)
    return dest


def _encode_png(png_path: Optional[str]) -> Optional[str]:
    if not png_path or not os.path.exists(png_path):
        return None
    with open(png_path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def _extract_and_render(step_path: str, profile: dict, tmpdir: str, part: str,
                        include_visualization: bool):
    """CPU-bound work for /extract/single — runs off the event loop via
    asyncio.to_thread so a slow/pathological file can be bounded by a timeout
    instead of tying up the request indefinitely. Raises on extraction failure;
    render failures are swallowed (logged) and simply omit the PNG, matching the
    previous inline behavior."""
    result = _extract.to_json(step_path, profile=profile)
    png_b64 = None
    if include_visualization:
        json_path = os.path.join(tmpdir, part + ".json")
        png_path = os.path.join(tmpdir, part + "_flat.png")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        try:
            _render.render(json_path, png_path)
            png_b64 = _encode_png(png_path)
        except Exception as render_exc:
            logger.warning("render skipped: %s", render_exc)
    return result, png_b64


# ── single ────────────────────────────────────────────────────────────────────

@router.post(
    "/extract/single",
    response_model=SingleExtractionResponse,
    status_code=200,
    summary="Extract a single STEP file (synchronous)",
    description=(
        "Upload one `.stp` / `.step` file and receive the full step-v2 JSON result "
        "plus a base-64 encoded flat-pattern PNG — all in one HTTP response. "
        "Processing is synchronous; the connection stays open until extraction completes."
    ),
)
async def extract_single(
    request: Request,
    file: UploadFile = File(..., description="STEP file to process"),
    profile_name: str = Form(
        default="default",
        description="Built-in bend profile name (default: 'default' — K=0.44)",
    ),
    profile_json: Optional[str] = Form(
        default=None,
        description=(
            "Inline custom bend profile as a JSON string. "
            "Overrides profile_name when supplied."
        ),
    ),
    include_visualization: bool = Form(
        default=True,
        description="Set to false to skip PNG generation (faster).",
    ),
):
    job_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    profile = _resolve_profile(profile_name, profile_json)

    with tempfile.TemporaryDirectory(prefix="busbarx_single_") as tmpdir:
        step_path = await _save_upload(file, tmpdir)
        part = os.path.splitext(os.path.basename(step_path))[0]

        logger.info("[job=%s] single extract: %s (profile=%s)", job_id, part, profile.get("name"))

        try:
            result, png_b64 = await asyncio.wait_for(
                asyncio.to_thread(_extract_and_render, step_path, profile, tmpdir,
                                  part, include_visualization),
                timeout=SINGLE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error("[job=%s] extraction timed out after %ss", job_id, SINGLE_TIMEOUT_S)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Extraction timed out after {SINGLE_TIMEOUT_S}s.",
            )
        except Exception as exc:
            logger.exception("[job=%s] extraction error", job_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Extraction failed: {exc}",
            )

    elapsed = round(time.perf_counter() - t0, 3)
    logger.info("[job=%s] done in %.2fs", job_id, elapsed)

    return SingleExtractionResponse(
        job_id=job_id,
        status=JobStatus.completed,
        result=result,
        visualization_b64=png_b64,
        elapsed_s=elapsed,
    )


# ── batch ─────────────────────────────────────────────────────────────────────

@router.post(
    "/extract/batch",
    response_model=BatchSubmitResponse,
    status_code=202,
    summary="Submit a batch of STEP files (asynchronous)",
    description=(
        "Upload up to 10 `.stp` / `.step` files. The server queues the job and "
        "returns immediately with a `job_id`. Poll `GET /v1/jobs/{job_id}` to check "
        "progress and retrieve results when `status == 'completed'`."
    ),
)
async def extract_batch(
    request: Request,
    files: List[UploadFile] = File(..., description="Up to 10 STEP files"),
    profile_name: str = Form(default="default"),
    profile_json: Optional[str] = Form(default=None),
):
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file is required.",
        )
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {MAX_FILES} files per batch request. Got {len(files)}.",
        )

    profile = _resolve_profile(profile_name, profile_json)

    # Save all uploads to a persistent temp dir (worker reads them later)
    # Use a dir name tied to the job_id so it survives until job completion
    store = get_store()
    job_id = store.create(file_count=len(files))
    upload_dir = tempfile.mkdtemp(prefix=f"busbarx_batch_{job_id}_")

    used_names: Set[str] = set()
    step_paths = []
    for upload in files:
        path = await _save_upload(upload, upload_dir, used=used_names)
        step_paths.append(path)

    logger.info("[job=%s] batch submitted: %d files, profile=%s",
                job_id, len(files), profile.get("name"))

    # Lazy-init: lifespan starts the pool normally; in test env it may not have run
    if not hasattr(request.app.state, "pool"):
        from ..services.worker import WorkerPool
        import os
        n = int(os.getenv("WORKER_THREADS", "4"))
        request.app.state.pool = WorkerPool(n)
        request.app.state.pool.start()
    pool = request.app.state.pool
    pool.submit(job_id, step_paths, profile, store, upload_dir=upload_dir)

    return BatchSubmitResponse(
        job_id=job_id,
        status=JobStatus.queued,
        file_count=len(files),
        poll_url=f"/v1/jobs/{job_id}",
    )


# ── profiles ──────────────────────────────────────────────────────────────────

@router.get(
    "/profiles",
    response_model=ProfilesResponse,
    summary="List available bend profiles",
)
async def list_profiles():
    """Returns all built-in bend profiles. Custom profiles can be passed inline
    in the `profile_json` field of any extraction request."""
    items = []
    for name, prof in bend_profiles.PROFILES.items():
        items.append(ProfileInfo(
            name=name,
            method=prof["method"],
            value=bend_profiles.resolved_value(prof),
        ))
    return ProfilesResponse(profiles=items)


@router.post(
    "/profiles/validate",
    response_model=ProfileValidateResponse,
    summary="Validate a custom bend profile JSON",
    description=(
        "Supply a raw JSON object for a custom bend profile. Returns whether "
        "the profile is valid for use in extraction requests. "
        "No STEP file is needed — this is a dry-run validation only."
    ),
)
async def validate_profile(body: dict):
    try:
        bend_profiles._validate(body, "<request body>")
        return ProfileValidateResponse(valid=True)
    except (ValueError, TypeError) as e:
        return ProfileValidateResponse(valid=False, error=str(e))
