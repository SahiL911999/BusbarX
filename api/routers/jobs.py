"""
Jobs router — /v1/jobs/{job_id}

GET    /v1/jobs/{job_id}   — poll status + retrieve result when completed
DELETE /v1/jobs/{job_id}   — cancel queued job or evict a finished one
"""
import logging
from fastapi import APIRouter, HTTPException, status

from ..models.responses import (
    BatchStatusResponse,
    ExtractionProgress,
    JobStatus,
    PartResult,
    StepV2Result,
)
from ..services.job_store import get_store

logger = logging.getLogger("busbarx.router.jobs")

router = APIRouter(tags=["Jobs"])


@router.get(
    "/jobs/{job_id}",
    response_model=BatchStatusResponse,
    summary="Poll batch job status and retrieve results",
    description=(
        "Poll this endpoint after submitting a batch request. "
        "When `status == 'completed'`, the `results` array is populated with "
        "per-file extraction results (including the base-64 PNG). "
        "When `status == 'failed'`, check the `error` field."
    ),
)
async def get_job(job_id: str):
    store = get_store()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id!r} not found. It may have expired or never existed.",
        )

    js = JobStatus(job["status"])
    progress = ExtractionProgress(**job["progress"]) if job.get("progress") else None

    # Build typed PartResult list when completed
    results = None
    if js == JobStatus.completed and job.get("results"):
        results = []
        for r in job["results"]:
            typed_result = None
            if r.get("result"):
                try:
                    typed_result = StepV2Result(**r["result"])
                except Exception as parse_exc:
                    logger.warning("Could not parse result for part %s: %s", r.get("part"), parse_exc)
            results.append(PartResult(
                part=r["part"],
                ok=r["ok"],
                result=typed_result,
                visualization_b64=r.get("visualization_b64"),
                error=r.get("error"),
            ))

    return BatchStatusResponse(
        job_id=job_id,
        status=js,
        progress=progress,
        results=results,
        elapsed_s=job.get("elapsed_s"),
        error=job.get("error"),
    )


@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel or evict a job",
    description=(
        "Cancel a queued job before it starts processing, or evict a completed/failed "
        "job to free memory. In-flight jobs cannot be cancelled mid-run."
    ),
)
async def delete_job(job_id: str):
    store = get_store()
    if not store.exists(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id!r} not found.",
        )
    job = store.get(job_id)
    if job and job["status"] == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot cancel a job that is currently processing. Wait for it to complete, then DELETE.",
        )
    store.delete(job_id)
    logger.info("Job %s evicted by client", job_id)
    # 204 No Content — no body
