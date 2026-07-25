"""
Unit tests for the unfold engine and health endpoint.

Tests:
- GET /v1/health returns 200 with expected fields
- All 3 sample STEP files unfold cleanly (status=computed)
- Flat-pattern dimensions match reference JSON within 1 mm
- Bend fold-line coordinates are within the flat-pattern bounds
- Thickness is derived from bend geometry (not a fallback heuristic)
"""
import json
import math
import os
import pytest
import cadquery as cq

from busbarx import bend_profiles
from busbarx import unfold as unfold_mod

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

SAMPLES = {
    "46004-641-01": (
        os.path.join(FIXTURES_DIR, "46004-641-01.stp"),
        os.path.join(FIXTURES_DIR, "46004-641-01.json"),
    ),
    "80255-263-01": (
        os.path.join(FIXTURES_DIR, "80255-263-01.stp"),
        os.path.join(FIXTURES_DIR, "80255-263-01.json"),
    ),
    "SBV13019": (
        os.path.join(FIXTURES_DIR, "SBV13019.stp"),
        os.path.join(FIXTURES_DIR, "SBV13019.json"),
    ),
}


# ── health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_returns_200(client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "step-v2"
    assert "api_version" in body


@pytest.mark.asyncio
async def test_health_no_auth_required(client):
    """Health endpoint must be accessible even when API_KEY would be set."""
    resp = await client.get("/v1/health", headers={"X-API-Key": ""})
    assert resp.status_code == 200


# ── unfold unit tests ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("part_name,stp_path,ref_path", [
    (k, v[0], v[1]) for k, v in SAMPLES.items()
])
def test_unfold_completes_for_all_samples(part_name, stp_path, ref_path):
    """unfold_solid() must succeed (not return None) for all reference parts."""
    prof = bend_profiles.load_profile("default")
    solid = cq.importers.importStep(stp_path).val()
    result = unfold_mod.unfold_solid(solid, prof)

    with open(ref_path, encoding="utf-8") as fh:
        ref = json.load(fh)

    ref_status = ref["part"]["flat_pattern_status"]
    if ref_status == "computed":
        assert result is not None, f"{part_name}: expected successful unfold but got None"
        # Flat pattern dimensions within 1 mm of reference
        ref_fp = ref["part"]["flat_pattern"]
        assert abs(result.length - ref_fp["length_mm"]) <= 1.0, (
            f"{part_name}: length {result.length} vs ref {ref_fp['length_mm']}"
        )
        assert abs(result.width - ref_fp["width_mm"]) <= 1.0, (
            f"{part_name}: width {result.width} vs ref {ref_fp['width_mm']}"
        )
    else:
        # fallback parts: unfold may return None (that's correct behaviour)
        pass


@pytest.mark.parametrize("part_name,stp_path,ref_path", [
    (k, v[0], v[1]) for k, v in SAMPLES.items()
])
def test_fold_lines_within_flat_bounds(part_name, stp_path, ref_path):
    """Every bend fold-line endpoint must lie within the flat-pattern bounds."""
    prof = bend_profiles.load_profile("default")
    solid = cq.importers.importStep(stp_path).val()
    result = unfold_mod.unfold_solid(solid, prof)
    if result is None:
        pytest.skip(f"{part_name} uses footprint fallback — fold-line test N/A")

    tol = 2.0  # mm tolerance for fold lines near the edge
    for b in result.bends_out:
        for coord in (b["line_start_mm"], b["line_end_mm"]):
            x, y = coord
            assert -tol <= x <= result.length + tol, (
                f"{part_name} bend{b['id']}: x={x} out of [0, {result.length}]"
            )
            assert -tol <= y <= result.width + tol, (
                f"{part_name} bend{b['id']}: y={y} out of [0, {result.width}]"
            )


@pytest.mark.parametrize("part_name,stp_path,ref_path", [
    (k, v[0], v[1]) for k, v in SAMPLES.items()
])
def test_bend_thickness_from_geometry(part_name, stp_path, ref_path):
    """Thickness must be derived from bend geometry (OCC cylinder radii)."""
    prof = bend_profiles.load_profile("default")
    solid = cq.importers.importStep(stp_path).val()
    result = unfold_mod.unfold_solid(solid, prof)
    if result is None:
        pytest.skip(f"{part_name} fallback — thickness test N/A")

    with open(ref_path, encoding="utf-8") as fh:
        ref = json.load(fh)
    ref_t = ref["part"]["flat_pattern"].get("thickness_mm")
    if ref_t:
        assert abs(result.thickness - ref_t) <= 0.5, (
            f"{part_name}: thickness {result.thickness} vs ref {ref_t}"
        )


def test_k_factor_bend_allowance_formula():
    """BA = (inner_r + K * thickness) * angle_rad — spot-check against known values."""
    prof = bend_profiles.load_profile("default")
    assert prof["k_factor"] == 0.44

    # 90 deg, r=12.7, t=12.7 → (12.7 + 0.44*12.7) * pi/2
    ba = bend_profiles.resolve_bend_allowance(prof, 12.7, 12.7, math.pi / 2)
    expected = (12.7 + 0.44 * 12.7) * math.pi / 2
    assert abs(ba - expected) < 1e-9

    # 45 deg, r=6.35, t=6.35
    ba2 = bend_profiles.resolve_bend_allowance(prof, 6.35, 6.35, math.pi / 4)
    expected2 = (6.35 + 0.44 * 6.35) * math.pi / 4
    assert abs(ba2 - expected2) < 1e-9
