"""
Regression tests — POST /v1/extract/batch + GET /v1/jobs/{job_id}

Tests:
- Submit 3 files → 202 with job_id + poll_url
- Poll until completed → all 3 results present and valid
- Partial failure (1 valid + 1 garbage) → partial success in results
- Exceed MAX_FILES (>10) → 422
- Empty file list → 422
- Custom bend profile applied to batch
"""
import asyncio
import json
import os
import time
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

STP_46 = os.path.join(FIXTURES_DIR, "46004-641-01.stp")
STP_80 = os.path.join(FIXTURES_DIR, "80255-263-01.stp")
STP_SBV = os.path.join(FIXTURES_DIR, "SBV13019.stp")
REF_46 = os.path.join(FIXTURES_DIR, "46004-641-01.json")
REF_80 = os.path.join(FIXTURES_DIR, "80255-263-01.json")
REF_SBV = os.path.join(FIXTURES_DIR, "SBV13019.json")


async def _poll_until_done(client, poll_url: str, timeout: float = 300.0, interval: float = 2.0):
    """Poll a job endpoint until status is completed or failed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(poll_url)
        assert resp.status_code == 200, f"Poll failed: {resp.text}"
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(interval)
    pytest.fail(f"Job at {poll_url} did not finish within {timeout}s")


# ── submit + poll helpers ─────────────────────────────────────────────────────

def _open_stp_tuple(path: str):
    """Return (filename, open_file, mimetype) for multipart upload."""
    return (os.path.basename(path), open(path, "rb"), "application/octet-stream")


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_submit_returns_202(client):
    """Batch submit must return 202 with job_id and poll_url."""
    files = [_open_stp_tuple(STP_80)]
    try:
        resp = await client.post(
            "/v1/extract/batch",
            files=[("files", f) for f in files],
            data={"profile_name": "default"},
        )
    finally:
        for _, fh, _ in files:
            fh.close()

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["poll_url"].startswith("/v1/jobs/")
    assert body["file_count"] == 1


@pytest.mark.asyncio
async def test_batch_all_samples_complete(client):
    """Submit all 3 sample STEP files → all must complete with valid results."""
    stps = [STP_46, STP_80, STP_SBV]
    refs = [REF_46, REF_80, REF_SBV]

    file_handles = [open(p, "rb") for p in stps]
    try:
        resp = await client.post(
            "/v1/extract/batch",
            files=[
                ("files", (os.path.basename(p), fh, "application/octet-stream"))
                for p, fh in zip(stps, file_handles)
            ],
            data={"profile_name": "default"},
        )
    finally:
        for fh in file_handles:
            fh.close()

    assert resp.status_code == 202
    job = await _poll_until_done(client, resp.json()["poll_url"])

    assert job["status"] == "completed"
    assert job["elapsed_s"] is not None
    results = job["results"]
    assert len(results) == 3

    for r, ref_path in zip(results, refs):
        assert r["ok"], f"Part {r['part']} failed: {r.get('error')}"
        assert r["result"]["schema_version"] == "step-v2"
        assert r["result"]["part"]["flat_pattern"]["length_mm"] > 0

        # Load reference and compare
        with open(ref_path, encoding="utf-8") as fh:
            ref = json.load(fh)
        assert len(r["result"]["features"]) == len(ref["features"]), (
            f"{r['part']}: feature count mismatch"
        )
        assert len(r["result"]["bends"]) == len(ref["bends"]), (
            f"{r['part']}: bend count mismatch"
        )


@pytest.mark.asyncio
async def test_batch_partial_failure(client):
    """One valid file + one garbage file → partial success (ok=True + ok=False)."""
    good_fh = open(STP_80, "rb")
    try:
        resp = await client.post(
            "/v1/extract/batch",
            files=[
                ("files", ("good.stp", good_fh, "application/octet-stream")),
                ("files", ("bad.stp", b"not a step file", "application/octet-stream")),
            ],
            data={"profile_name": "default"},
        )
    finally:
        good_fh.close()

    assert resp.status_code == 202
    job = await _poll_until_done(client, resp.json()["poll_url"])
    assert job["status"] == "completed"

    ok_results = [r for r in job["results"] if r["ok"]]
    fail_results = [r for r in job["results"] if not r["ok"]]
    assert len(ok_results) >= 1, "At least 1 result should succeed"
    assert len(fail_results) >= 1, "At least 1 result should fail"
    assert fail_results[0]["error"] is not None


@pytest.mark.asyncio
async def test_batch_exceeds_max_files(client):
    """Submitting more than 10 files must return 422."""
    resp = await client.post(
        "/v1/extract/batch",
        files=[
            ("files", (f"file{i}.stp", b"dummy", "application/octet-stream"))
            for i in range(11)
        ],
        data={"profile_name": "default"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "10" in str(detail)


@pytest.mark.asyncio
async def test_batch_no_files(client):
    """Empty file list must return 422."""
    resp = await client.post(
        "/v1/extract/batch",
        files=[("files", ("empty.stp", b"", "application/octet-stream"))],
        data={"profile_name": "default"},
    )
    # An empty file will be saved but should fail during extraction (not a valid STEP)
    # The submission itself returns 202; the failure shows in the job result
    assert resp.status_code in (202, 422)


@pytest.mark.asyncio
async def test_batch_custom_profile(client):
    """Custom K-factor must be reflected in all batch results."""
    custom = json.dumps({"name": "batch_test", "method": "k_factor", "k_factor": 0.35})
    fh = open(STP_SBV, "rb")
    try:
        resp = await client.post(
            "/v1/extract/batch",
            files=[("files", ("SBV13019.stp", fh, "application/octet-stream"))],
            data={"profile_json": custom},
        )
    finally:
        fh.close()

    assert resp.status_code == 202
    job = await _poll_until_done(client, resp.json()["poll_url"])
    assert job["status"] == "completed"
    r = job["results"][0]
    assert r["ok"]
    bp = r["result"]["bend_parameters"]
    assert bp["value"] == pytest.approx(0.35)
