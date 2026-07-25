"""
Optional API-key middleware.

Enabled ONLY when the API_KEY environment variable is set.
When enabled, every request must supply the header:
    X-API-Key: <value>

The /v1/health endpoint is always exempt (for Render's health-check probe).
All other unauthenticated requests receive 403.
"""
import os
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("busbarx.auth")

EXEMPT_PATHS = {"/v1/health", "/", "/docs", "/redoc", "/openapi.json"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        self._api_key = os.getenv("API_KEY", "").strip() or None
        if self._api_key:
            logger.info("API key auth ENABLED")
        else:
            logger.info("API key auth DISABLED (set API_KEY env var to enable)")

    async def dispatch(self, request: Request, call_next):
        # Auth disabled — pass through
        if self._api_key is None:
            return await call_next(request)

        # Exempt paths (health checks, docs)
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Validate key
        provided = request.headers.get("X-API-Key", "").strip()
        if provided != self._api_key:
            logger.warning(
                "Unauthorized request to %s from %s",
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=403,
                content={
                    "type": "https://busbarx.io/errors/forbidden",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": "Missing or invalid X-API-Key header.",
                },
            )

        return await call_next(request)
