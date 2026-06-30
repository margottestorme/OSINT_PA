import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from loguru import logger


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware qui logge chaque requête API avec méthode, path, user et status code."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()

        response = await call_next(request)

        duration_ms = (time.monotonic() - start) * 1000
        user = request.state.user if hasattr(request.state, "user") else "anonymous"

        logger.info(
            f"{request.method} {request.url.path} | "
            f"user={user} | status={response.status_code} | "
            f"{duration_ms:.1f}ms"
        )

        return response
