from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.core.config import get_settings, validate_security
from app.core.logging import setup_logging
from app.api.routes import router
from app.api.face_routes import face_router
from app.api.middleware import (
    GlobalRateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)

settings = get_settings()
setup_logging()
validate_security(settings)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Ordre d'exécution (externe -> interne) : SecurityHeaders, RequestLogging, CORS, GlobalRateLimit.
# Starlette empile en LIFO : le DERNIER add_middleware est le plus externe.
# SecurityHeaders doit être le plus externe pour que TOUTE réponse (y compris un 429
# émis par GlobalRateLimit) reçoive les 5 en-têtes de sécurité. RequestLogging est
# externe au rate-limit pour que les réponses 429 soient également journalisées.
app.add_middleware(GlobalRateLimitMiddleware, max_requests=settings.GLOBAL_RATE_LIMIT)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.ALLOWED_ORIGINS.split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(router)
app.include_router(face_router)

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


# --- Pages frontend ---

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page():
    return (FRONTEND_DIR / "dashboard.html").read_text(encoding="utf-8")


@app.get("/report", response_class=HTMLResponse, include_in_schema=False)
async def report_page():
    return (FRONTEND_DIR / "report.html").read_text(encoding="utf-8")


@app.get("/results", response_class=HTMLResponse, include_in_schema=False)
async def results_page():
    return (FRONTEND_DIR / "results.html").read_text(encoding="utf-8")


@app.get("/graph", response_class=HTMLResponse, include_in_schema=False)
async def graph_page():
    return (FRONTEND_DIR / "graph.html").read_text(encoding="utf-8")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erreur non gérée sur {request.method} {request.url.path} : {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
    )
