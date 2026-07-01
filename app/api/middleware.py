import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from loguru import logger

# CSP : 'self' par défaut, avec allowlist des CDN utilisés par le frontend
# (Lucide, vis-network, police Inter). 'unsafe-inline' est nécessaire pour les
# petits scripts d'init inline (lucide.createIcons) et les styles inline des pages.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": _CSP,
}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        return response


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60, purge_interval: int = 300):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._purge_interval = purge_interval
        self._last_purge = time.monotonic()
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _purge_expired(self, now: float) -> None:
        """Supprime les IPs dont tous les timestamps sont expirés (évite la fuite mémoire)."""
        if now - self._last_purge < self._purge_interval:
            return
        stale = [
            ip for ip, ts in self._requests.items()
            if not ts or now - ts[-1] >= self.window
        ]
        for ip in stale:
            del self._requests[ip]
        self._last_purge = now
        if stale:
            logger.debug(f"[rate_limit] Purge de {len(stale)} IP(s) inactive(s)")

    async def dispatch(self, request: Request, call_next) -> Response:
        ip = self._get_ip(request)
        now = time.monotonic()

        self._purge_expired(now)

        timestamps = self._requests[ip]
        self._requests[ip] = [t for t in timestamps if now - t < self.window]

        if len(self._requests[ip]) >= self.max_requests:
            logger.warning(f"[rate_limit] IP {ip} a dépassé {self.max_requests} req/{self.window}s")
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes — réessayez plus tard"},
            )

        self._requests[ip].append(now)
        return await call_next(request)
