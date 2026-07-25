# ─────────────────────────────────────────────────────────────────────────────
# BusbarX Nexus API — Dockerfile
#
# Multi-stage build:
#   builder  → installs Python deps into an isolated venv
#   runtime  → copies only the venv + app code; runs as non-root
#
# CadQuery / OCC requires several system libraries. We use python:3.13-slim
# as base and install the minimum required apt packages.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# System libraries required by OCC / cadquery binary wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglu1-mesa \
        libgomp1 \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
        libx11-6 \
        libxt6 \
        libfontconfig1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip / wheel first (avoids legacy bdist_wheel issues with OCC)
RUN pip install --upgrade pip wheel setuptools

# Copy and install dependencies
COPY requirements.txt /tmp/requirements.txt

# Install WITHOUT customtkinter (GUI not needed on the server)
RUN grep -v "customtkinter" /tmp/requirements.txt > /tmp/server_requirements.txt \
    && pip install --no-cache-dir -r /tmp/server_requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Same runtime libs (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglu1-mesa \
        libgomp1 \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
        libx11-6 \
        libxt6 \
        libfontconfig1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user for security
RUN groupadd -r busbarx && useradd -r -g busbarx -d /app -s /sbin/nologin busbarx

WORKDIR /app

# Copy application code
COPY busbarx/ ./busbarx/
COPY api/ ./api/

# Set ownership
RUN chown -R busbarx:busbarx /app

USER busbarx

# Matplotlib headless (no display required)
ENV MPLBACKEND=Agg
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Render sets $PORT dynamically; default to 8000 locally
ENV PORT=8000

EXPOSE 8000

# Render's health check calls GET /v1/health before routing traffic
# (Render ignores Docker HEALTHCHECK; it uses healthCheckPath in render.yaml)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT}/v1/health || exit 1

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level ${LOG_LEVEL:-info}"]
