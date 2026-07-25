# BusbarX Nexus API

Production FastAPI service wrapping the BusbarX STEP-extraction engine. Converts
busbar `.stp`/`.step` files into structured **step-v2 JSON** — true flat-pattern
(unfolded) coordinates, features, bends — plus a flat-pattern visualization PNG.

## Deployment on Render.com

> **Use Web Service — NOT Background Worker.**
> Background Workers have no public URL. Select **Web Service** with **Docker runtime**.

### Steps
1. Push this repo to GitHub
2. On Render → New → Web Service → connect your repo
3. Render auto-detects `render.yaml` and configures the service
4. Select **Standard (2 GB RAM)** plan minimum (OCC requires it)
5. Set any secrets (e.g. `API_KEY`) in the Render dashboard under Environment
6. Deploy — Render calls `GET /v1/health` before routing traffic

Auto-deploy is triggered on every push to `main`.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/extract/single` | Extract one STEP file (synchronous, result in response) |
| `POST` | `/v1/extract/batch` | Submit up to 10 files (async, returns 202 + `job_id`) |
| `GET` | `/v1/jobs/{job_id}` | Poll batch job status / retrieve results |
| `DELETE` | `/v1/jobs/{job_id}` | Evict a completed/queued job |
| `GET` | `/v1/profiles` | List built-in bend profiles |
| `POST` | `/v1/profiles/validate` | Validate a custom bend profile JSON |
| `GET` | `/v1/health` | Liveness probe (Render health check) |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/redoc` | ReDoc documentation |

---

## Quick Start (local)

### Option A — Docker Compose (recommended)
```bash
cp .env.example .env
docker compose up --build
# API available at http://localhost:8000
# Swagger UI at http://localhost:8000/docs
```

### Option B — Direct Python
```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

---

## Usage Examples

### Single file extraction
```bash
curl -X POST http://localhost:8000/v1/extract/single \
  -F "file=@SBV13019.stp" \
  -F "profile_name=default" \
  -F "include_visualization=true"
```

**Response (200):**
```json
{
  "job_id": "uuid4",
  "status": "completed",
  "elapsed_s": 4.12,
  "result": { "schema_version": "step-v2", "part": { ... }, "features": [ ... ], "bends": [ ... ] },
  "visualization_b64": "<base64-PNG>"
}
```

### Batch extraction
```bash
# Submit
curl -X POST http://localhost:8000/v1/extract/batch \
  -F "files=@part1.stp" \
  -F "files=@part2.stp" \
  -F "files=@part3.stp" \
  | jq .job_id

# Poll
curl http://localhost:8000/v1/jobs/<job_id>
```

### Custom bend profile (inline)
```bash
curl -X POST http://localhost:8000/v1/extract/single \
  -F "file=@part.stp" \
  -F 'profile_json={"name":"my_shop","method":"k_factor","k_factor":0.38}'
```

### Validate a profile without running extraction
```bash
curl -X POST http://localhost:8000/v1/profiles/validate \
  -H "Content-Type: application/json" \
  -d '{"name":"acme","method":"k_factor","k_factor":0.42}'
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Server port (Render sets this automatically) |
| `WORKER_THREADS` | `4` | Background thread pool size |
| `LOG_LEVEL` | `info` | `debug` / `info` / `warning` / `error` |
| `API_KEY` | *(unset)* | Enables `X-API-Key` auth when set |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `SINGLE_TIMEOUT_S` | `120` | Timeout for synchronous extraction |
| `MPLBACKEND` | `Agg` | Matplotlib backend (must stay `Agg` — no display) |

---

## Running Tests
```bash
# Install test deps (included in requirements.txt)
pip install -r requirements.txt

# Run full suite
pytest tests/ -v --tb=short

# Smoke test — single endpoint only
pytest tests/test_extract_single.py -v

# Unit tests only (no HTTP server needed)
pytest tests/test_unfold.py tests/test_bend_profiles.py -v
```

---

## Architecture

```
busbarx-api/
├── busbarx/           ← STEP extraction engine (unchanged from Milestone 3)
│   ├── extract.py     STEP → step-v2 JSON
│   ├── unfold.py      flat-pattern BFS unfold engine
│   ├── bend_profiles.py  configurable bend allowance (K-factor etc.)
│   ├── render.py      matplotlib PNG renderer
│   └── pipeline.py    batch driver (used by worker.py)
│
├── api/
│   ├── main.py        FastAPI app + lifespan + middleware
│   ├── routers/
│   │   ├── extract.py    POST /v1/extract/single + /batch + /profiles
│   │   └── jobs.py       GET/DELETE /v1/jobs/{job_id}
│   ├── models/
│   │   ├── requests.py   Pydantic request models
│   │   └── responses.py  Pydantic response models (step-v2 schema)
│   ├── services/
│   │   ├── job_store.py  thread-safe in-memory job store
│   │   └── worker.py     ThreadPoolExecutor extraction worker
│   └── middleware/
│       └── auth.py       optional X-API-Key middleware
│
├── tests/             regression + unit test suite
├── Dockerfile         multi-stage build (builder + runtime)
├── docker-compose.yml local dev
├── render.yaml        Render.com infrastructure-as-code
└── .env.example       environment variable template
```

## Render Instance Sizing

| Load | Recommended Instance | RAM |
|---|---|---|
| Dev / staging | Standard | 2 GB |
| Production (light) | Pro | 4 GB |
| Production (concurrent batch) | Pro Plus | 8 GB |

CadQuery/OCC is memory-intensive. Never use the Starter (512 MB) or Free tier.
