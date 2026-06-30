from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.api.routes import router
from app.api.face_routes import face_router
from app.api.middleware import RequestLoggingMiddleware

settings = get_settings()
setup_logging()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(RequestLoggingMiddleware)
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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erreur non gérée sur {request.method} {request.url.path} : {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
    )
