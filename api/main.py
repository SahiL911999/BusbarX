"""
BusbarX Nexus — Production FastAPI Application

Entry point: `api/main.py`
Start with: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .middleware.auth import APIKeyMiddleware
from .routers import extract, jobs
from .services.worker import WorkerPool

# ── logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("busbarx.api")


# ── lifespan (start / stop worker pool) ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    n_threads = int(os.getenv("WORKER_THREADS", "4"))
    logger.info("Starting BusbarX API — worker threads: %d", n_threads)
    app.state.pool = WorkerPool(n_threads)
    app.state.pool.start()
    yield
    logger.info("Shutting down worker pool…")
    app.state.pool.stop()


# ── app ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="BusbarX Nexus API",
    description=(
        "Production REST API for STEP → flat-pattern extraction. "
        "Converts busbar STEP files into structured step-v2 JSON with "
        "true flat-pattern (unfolded) coordinates, features, bends, and "
        "a visualization PNG."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── optional API-key auth ─────────────────────────────────────────────────────
# Enabled only when API_KEY env var is set; skips /v1/health automatically.
app.add_middleware(APIKeyMiddleware)


# ── request-id + timing middleware ────────────────────────────────────────────
@app.middleware("http")
async def request_meta(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = round(time.perf_counter() - t0, 4)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = str(elapsed)
    return response


# ── global exception handler (RFC 7807 Problem JSON) ─────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("Unhandled error [%s]: %s", request_id, exc)
    return JSONResponse(
        status_code=500,
        content={
            "type": "https://busbarx.io/errors/internal",
            "title": "Internal Server Error",
            "status": 500,
            "detail": str(exc),
            "request_id": request_id,
        },
    )


# ── routers ───────────────────────────────────────────────────────────────────
app.include_router(extract.router, prefix="/v1")
app.include_router(jobs.router, prefix="/v1")


# ── health + info ─────────────────────────────────────────────────────────────
@app.get("/v1/health", tags=["System"], summary="Liveness probe")
async def health():
    """Render / k8s liveness probe. Returns 200 when the service is ready."""
    return {
        "status": "ok",
        "version": "step-v2",
        "api_version": "1.0.0",
        "worker_threads": int(os.getenv("WORKER_THREADS", "4")),
    }


@app.get("/", include_in_schema=False)
async def root():
    return {"message": "BusbarX Nexus API — see /docs for the interactive spec."}
