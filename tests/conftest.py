"""
Shared pytest fixtures for BusbarX API tests.
Uses httpx AsyncClient with the FastAPI ASGI app — no real server needed.
"""
import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from api.main import app

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

SAMPLE_STPS = {
    "46004-641-01": os.path.join(FIXTURES_DIR, "46004-641-01.stp"),
    "80255-263-01": os.path.join(FIXTURES_DIR, "80255-263-01.stp"),
    "SBV13019":     os.path.join(FIXTURES_DIR, "SBV13019.stp"),
}
SAMPLE_JSONS = {
    "46004-641-01": os.path.join(FIXTURES_DIR, "46004-641-01.json"),
    "80255-263-01": os.path.join(FIXTURES_DIR, "80255-263-01.json"),
    "SBV13019":     os.path.join(FIXTURES_DIR, "SBV13019.json"),
}


@pytest_asyncio.fixture
async def client():
    """ASGI test client — app lifecycle (startup/shutdown) is exercised."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
def stp_bytes(name="SBV13019"):
    path = SAMPLE_STPS[name]
    with open(path, "rb") as fh:
        return fh.read()


@pytest.fixture
def all_stp_paths():
    return list(SAMPLE_STPS.values())
