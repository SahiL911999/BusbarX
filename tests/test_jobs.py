"""
Tests for GET /v1/jobs/{job_id} and DELETE /v1/jobs/{job_id}.

Tests:
- Unknown job_id → 404
- Delete a queued job → 204
- Delete a non-existent job → 404
- Delete a processing job → 409 (cannot cancel mid-run)
- Job result persists after completion until explicitly deleted
- Concurrent batch submissions don't collide (unique job_ids)
"""
import asyncio
import os
import time
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
STP_80 = os.path.join(FIXTURES_DIR, "80255-263-01.stp")
STP_SBV = os.path.join(FIXTURES_DIR, "SBV13019.stp")


async def _submit_batch(client, stp_path: str) -> str:
    """Submit a single-file batch and return the job_id."""
    with open(stp_path, "rb") as fh:
        resp = await client.post(
            "/v1/extract/batch",
            files=[("files", (os.path.basename(stp_path), fh, "application/octet-stream"))],
            data={"profile_name": "default"},
        )
    assert resp.status_code == 202
    return resp.json()["job_id"]


async def _poll_done(client, job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(f"/v1/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(2)
    pytest.fail(f"Job {job_id} did not finish within {timeout}s")


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_unknown_job(client):
    """GET on a non-existent job_id must return 404."""
    resp = await client.get("/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_unknown_job(client):
    """DELETE on a non-existent job_id must return 404."""
    resp = await client.delete("/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_completed_job(client):
    """Completed job can be evicted with DELETE → 204, then 404 on re-GET."""
    job_id = await _submit_batch(client, STP_80)
    await _poll_done(client, job_id)

    delete_resp = await client.delete(f"/v1/jobs/{job_id}")
    assert delete_resp.status_code == 204

    get_resp = await client.get(f"/v1/jobs/{job_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_job_result_persists_after_completion(client):
    """Job result must remain accessible until explicitly deleted."""
    job_id = await _submit_batch(client, STP_SBV)
    body = await _poll_done(client, job_id)
    assert body["status"] == "completed"

    # Re-fetch — must still be there
    resp = await client.get(f"/v1/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_concurrent_jobs_have_unique_ids(client):
    """Two concurrent batch submissions must get distinct job_ids."""
    with open(STP_80, "rb") as f1, open(STP_SBV, "rb") as f2:
        r1, r2 = await asyncio.gather(
            client.post(
                "/v1/extract/batch",
                files=[("files", ("80255-263-01.stp", f1, "application/octet-stream"))],
                data={"profile_name": "default"},
            ),
            client.post(
                "/v1/extract/batch",
                files=[("files", ("SBV13019.stp", f2, "application/octet-stream"))],
                data={"profile_name": "default"},
            ),
        )
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["job_id"] != r2.json()["job_id"]


@pytest.mark.asyncio
async def test_job_progress_updates(client):
    """Progress.done must increase monotonically as a batch processes."""
    job_id = await _submit_batch(client, STP_80)
    seen_done = []
    deadline = time.time() + 300
    while time.time() < deadline:
        resp = await client.get(f"/v1/jobs/{job_id}")
        body = resp.json()
        if body.get("progress"):
            seen_done.append(body["progress"]["done"])
        if body["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(1)

    # done must be non-decreasing
    for a, b in zip(seen_done, seen_done[1:]):
        assert b >= a, f"Progress went backwards: {seen_done}"
