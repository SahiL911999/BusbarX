"""
Tests for GET /v1/profiles and POST /v1/profiles/validate.

Tests:
- GET /v1/profiles returns at least the default profile
- Default profile has method=k_factor and a numeric value
- POST /v1/profiles/validate accepts valid k_factor profile
- POST /v1/profiles/validate accepts valid bend_deduction profile
- POST /v1/profiles/validate rejects missing method
- POST /v1/profiles/validate rejects unknown method
- POST /v1/profiles/validate rejects k_factor with non-numeric k_factor
- POST /v1/profiles/validate rejects bend_deduction without deduction_table
"""
import pytest


@pytest.mark.asyncio
async def test_get_profiles_returns_default(client):
    """GET /v1/profiles must include the built-in 'default' profile."""
    resp = await client.get("/v1/profiles")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["name"] for p in body["profiles"]]
    assert "default" in names


@pytest.mark.asyncio
async def test_default_profile_is_k_factor(client):
    """The default profile must be k_factor with a numeric value."""
    resp = await client.get("/v1/profiles")
    assert resp.status_code == 200
    default = next(p for p in resp.json()["profiles"] if p["name"] == "default")
    assert default["method"] == "k_factor"
    assert isinstance(default["value"], (int, float))
    assert 0.0 < default["value"] < 1.0, "K-factor should be between 0 and 1"


@pytest.mark.asyncio
async def test_validate_valid_k_factor(client):
    """Valid k_factor profile must be accepted."""
    resp = await client.post(
        "/v1/profiles/validate",
        json={"name": "test", "method": "k_factor", "k_factor": 0.40},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True
    assert resp.json()["error"] is None


@pytest.mark.asyncio
async def test_validate_valid_bend_deduction(client):
    """Valid bend_deduction profile must be accepted."""
    resp = await client.post(
        "/v1/profiles/validate",
        json={
            "name": "press_brake",
            "method": "bend_deduction",
            "deduction_table": {"90": 5.2, "45": 2.6},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


@pytest.mark.asyncio
async def test_validate_missing_method(client):
    """Profile without 'method' must fail validation."""
    resp = await client.post(
        "/v1/profiles/validate",
        json={"name": "bad", "k_factor": 0.44},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert resp.json()["error"] is not None


@pytest.mark.asyncio
async def test_validate_unknown_method(client):
    """Profile with an unknown method must fail validation."""
    resp = await client.post(
        "/v1/profiles/validate",
        json={"name": "weird", "method": "magic_numbers", "k_factor": 0.44},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_k_factor_non_numeric(client):
    """k_factor value that is a string must fail validation."""
    resp = await client.post(
        "/v1/profiles/validate",
        json={"name": "bad_k", "method": "k_factor", "k_factor": "high"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_bend_deduction_missing_table(client):
    """bend_deduction without deduction_table must fail validation."""
    resp = await client.post(
        "/v1/profiles/validate",
        json={"name": "no_table", "method": "bend_deduction"},
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
