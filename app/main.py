from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.core.config import get_settings, validate_security
from app.core.logging import setup_logging
from app.search.elastic import elastic
from app.api.routes import router
from app.api.face_routes import face_router
from app.api.stream_routes import stream_router
from app.api.investigation_routes import investigation_router
from app.api.username_routes import username_router
from app.api.middleware import (
    GlobalRateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)

settings = get_settings()
setup_logging()
validate_security(settings)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles qui force la revalidation à chaque requête (Cache-Control: no-cache).

    Starlette gère déjà ETag/Last-Modified (le navigateur envoie If-None-Match, reçoit un
    304 si rien n'a changé — donc pas de re-téléchargement inutile), mais SANS en-tête
    Cache-Control explicite, certains navigateurs appliquent un cache heuristique et
    servent un JS/CSS obsolète sans même revalider auprès du serveur. Projet sans étape de
    build (pas de hash de version dans les noms de fichiers) : plusieurs itérations sur
    app.js dans une même session de dev ont fini par servir une version périmée malgré un
    rechargement forcé côté navigateur (2026-07-09) — `no-cache` élimine la classe de bug.
    """
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Cycle de vie applicatif — initialise Elasticsearch (Module 11) en best-effort.

    ES est OPTIONNEL : toute erreur d'initialisation est journalisée mais n'empêche
    jamais le démarrage (dégradation propre). Les index sont créés s'ils sont absents.
    """
    try:
        await elastic.connect()
        await elastic.create_indices()
    except Exception as e:  # garde-fou : ES ne doit jamais bloquer le démarrage
        logger.warning(f"[startup] Initialisation Elasticsearch ignorée : {e}")
    yield
    try:
        await elastic.disconnect()
    except Exception as e:
        logger.warning(f"[shutdown] Fermeture Elasticsearch : {e}")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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
    expose_headers=["X-Total-Count"],  # pagination : total exposé en en-tête (GET /api/faces/persons)
)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(router)
app.include_router(face_router)
app.include_router(stream_router)
app.include_router(investigation_router)
app.include_router(username_router)

app.mount("/static", NoCacheStaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


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


@app.get("/search", response_class=HTMLResponse, include_in_schema=False)
async def search_page():
    return (FRONTEND_DIR / "search.html").read_text(encoding="utf-8")


@app.get("/graph", response_class=HTMLResponse, include_in_schema=False)
async def graph_page():
    return (FRONTEND_DIR / "graph.html").read_text(encoding="utf-8")


@app.get("/investigations", response_class=HTMLResponse, include_in_schema=False)
async def investigations_page():
    return (FRONTEND_DIR / "investigations.html").read_text(encoding="utf-8")


@app.get("/investigation_detail", response_class=HTMLResponse, include_in_schema=False)
async def investigation_detail_page():
    return (FRONTEND_DIR / "investigation_detail.html").read_text(encoding="utf-8")


@app.get("/username", response_class=HTMLResponse, include_in_schema=False)
async def username_page():
    return (FRONTEND_DIR / "username.html").read_text(encoding="utf-8")


@app.get("/stream", response_class=HTMLResponse, include_in_schema=False)
async def stream_page():
    return (FRONTEND_DIR / "stream.html").read_text(encoding="utf-8")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erreur non gérée sur {request.method} {request.url.path} : {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur"},
    )
