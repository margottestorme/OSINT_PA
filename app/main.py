from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.api.routes import router
from app.api.face_routes import face_router
from app.api.middleware import RequestLoggingMiddleware

settings = get_settings()
setup_logging()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(RequestLoggingMiddleware)
app.include_router(router)
app.include_router(face_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Intercepte toutes les exceptions non gérées — jamais de stack trace exposée."""
    logger.error(f"Erreur non gérée sur {request.method} {request.url.path} : {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
    )
