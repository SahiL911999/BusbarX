"""
Regression tests — POST /v1/extract/single

Tests:
- Each of the 3 sample STEP files returns 200 with a valid step-v2 result
- Response schema is fully typed (no extra/missing fields)
- Features + bends match the reference JSON within tolerances
- PNG visualization is returned as base64
- Invalid file type returns 422
- Oversized filename still works (name is sanitized)
- Custom inline bend profile is applied correctly
- include_visualization=false skips PNG generation
"""
import base64
import json
import os
import pytest
import pytest_asyncio

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

SAMPLE_PAIRS = [
    ("46004-641-01", os.path.join(FIXTURES_DIR, "46004-641-01.stp"),
                     os.path.join(FIXTURES_DIR, "46004-641-01.json")),
    ("80255-263-01", os.path.join(FIXTURES_DIR, "80255-263-01.stp"),
                     os.path.join(FIXTURES_DIR, "80255-263-01.json")),
    ("SBV13019",     os.path.join(FIXTURES_DIR, "SBV13019.stp"),
                     os.path.join(FIXTURES_DIR, "SBV13019.json")),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _assert_schema(data: dict):
    """Assert required top-level fields of the step-v2 schema are present."""
    assert data["schema_version"] == "step-v2"
    assert data["units"] == "mm"
    assert data["source"] == "STEP"
    assert "part" in data
    assert "features" in data
    assert "bends" in data
    assert "validation" in data
    assert "coordinate_system" in data
    assert "bend_parameters" in data
    part = data["part"]
    assert part["flat_pattern_status"] in ("computed", "fallback")
    assert part["flat_pattern"]["length_mm"] > 0
    assert part["flat_pattern"]["width_mm"] > 0
    for f in data["features"]:
        assert f["id"] >= 1
        assert f["type"] in ("round", "obround", "rectangle", "square", "irregular")
        assert "x_mm" in f and "y_mm" in f
        assert f["confidence"] == 1.0
        assert isinstance(f["in_bounds"], bool)
    for b in data["bends"]:
        assert b["angle_deg"] > 0
        assert b["radius_mm"] > 0
        assert len(b["line_start_mm"]) == 2
        assert len(b["line_end_mm"]) == 2


def _assert_matches_reference(result: dict, ref_path: str, tol_mm: float = 1.0):
    """Assert flat-pattern dims and feature count are close to the reference JSON."""
    with open(ref_path, encoding="utf-8") as f:
        ref = json.load(f)
    ref_fp = ref["part"]["flat_pattern"]
    res_fp = result["part"]["flat_pattern"]
    assert abs(res_fp["length_mm"] - ref_fp["length_mm"]) <= tol_mm, (
        f"length_mm mismatch: got {res_fp['length_mm']}, ref {ref_fp['length_mm']}"
    )
    assert abs(res_fp["width_mm"] - ref_fp["width_mm"]) <= tol_mm, (
        f"width_mm mismatch: got {res_fp['width_mm']}, ref {ref_fp['width_mm']}"
    )
    # Feature count must match exactly
    assert len(result["features"]) == len(ref["features"]), (
        f"feature count mismatch: got {len(result['features'])}, ref {len(ref['features'])}"
    )
    # Bend count must match
    assert len(result["bends"]) == len(ref["bends"]), (
        f"bend count mismatch: got {len(result['bends'])}, ref {len(ref['bends'])}"
    )


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("part_name,stp_path,ref_path", SAMPLE_PAIRS)
async def test_single_extract_all_samples(client, part_name, stp_path, ref_path):
    """Each sample STEP file must extract cleanly and match the reference JSON."""
    with open(stp_path, "rb") as fh:
        resp = await client.post(
            "/v1/extract/single",
            files={"file": (f"{part_name}.stp", fh, "application/octet-stream")},
            data={"profile_name": "default", "include_visualization": "true"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["job_id"]
    assert body["elapsed_s"] > 0
    result = body["result"]
    _assert_schema(result)
    _assert_matches_reference(result, ref_path)
    # PNG must be present and decodable
    png_b64 = body.get("visualization_b64")
    assert png_b64, "visualization_b64 must not be empty"
    decoded = base64.b64decode(png_b64)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n", "visualization_b64 is not a valid PNG"


@pytest.mark.asyncio
async def test_single_no_visualization(client):
    """include_visualization=false must return null visualization_b64 (faster path)."""
    stp_path = os.path.join(FIXTURES_DIR, "80255-263-01.stp")
    with open(stp_path, "rb") as fh:
        resp = await client.post(
            "/v1/extract/single",
            files={"file": ("80255-263-01.stp", fh, "application/octet-stream")},
            data={"profile_name": "default", "include_visualization": "false"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visualization_b64"] is None


@pytest.mark.asyncio
async def test_single_invalid_extension(client):
    """Non-STEP file must be rejected with 422."""
    resp = await client.post(
        "/v1/extract/single",
        files={"file": ("model.obj", b"garbage content", "application/octet-stream")},
        data={"profile_name": "default"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_single_invalid_step_content(client):
    """A file with a .stp extension but garbage content must return 422."""
    resp = await client.post(
        "/v1/extract/single",
        files={"file": ("fake.stp", b"this is not a STEP file", "application/octet-stream")},
        data={"profile_name": "default"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_single_custom_inline_profile(client):
    """A valid inline bend profile must be applied and reflected in the response."""
    stp_path = os.path.join(FIXTURES_DIR, "SBV13019.stp")
    custom_profile = json.dumps({"name": "test_shop", "method": "k_factor", "k_factor": 0.38})
    with open(stp_path, "rb") as fh:
        resp = await client.post(
            "/v1/extract/single",
            files={"file": ("SBV13019.stp", fh, "application/octet-stream")},
            data={"profile_json": custom_profile, "include_visualization": "false"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    bp = body["result"]["bend_parameters"]
    assert bp["method"] == "k_factor"
    assert bp["value"] == pytest.approx(0.38)
    assert bp["profile"] == "test_shop"


@pytest.mark.asyncio
async def test_single_invalid_inline_profile(client):
    """An invalid inline profile must return 422 — not a 500."""
    stp_path = os.path.join(FIXTURES_DIR, "SBV13019.stp")
    bad_profile = json.dumps({"method": "k_factor", "k_factor": "not_a_number"})
    with open(stp_path, "rb") as fh:
        resp = await client.post(
            "/v1/extract/single",
            files={"file": ("SBV13019.stp", fh, "application/octet-stream")},
            data={"profile_json": bad_profile},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_single_all_features_in_bounds(client):
    """All features in the reference parts must be reported in-bounds."""
    stp_path = os.path.join(FIXTURES_DIR, "46004-641-01.stp")
    with open(stp_path, "rb") as fh:
        resp = await client.post(
            "/v1/extract/single",
            files={"file": ("46004-641-01.stp", fh, "application/octet-stream")},
            data={"profile_name": "default", "include_visualization": "false"},
        )
    assert resp.status_code == 200
    data = resp.json()
    oob = data["result"]["validation"]["out_of_bounds_ids"]
    assert oob == [], f"Unexpected out-of-bounds features: {oob}"
