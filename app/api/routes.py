from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.config import get_settings
from app.core.security import authenticate_user, create_access_token, verify_token
from app.db.session import get_db
from app.db.models import Correlation, Person, SearchHistory
from app.schemas.schemas import (
    CorrelationResponse,
    HealthResponse,
    LoginRequest,
    PersonResponse,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    TokenResponse,
)
from app.api.rate_limit import search_rate_limiter

router = APIRouter()


# --- Healthcheck (public) ---

@router.get("/health", response_model=HealthResponse, tags=["système"])
async def health_check():
    settings = get_settings()
    return HealthResponse(status="ok", version=settings.APP_VERSION)


# --- Auth ---

@router.post("/api/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(body: LoginRequest):
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides",
        )
    token = create_access_token({"sub": user}, expires_minutes=15)
    logger.info(f"[auth] Connexion réussie pour {user}")
    return TokenResponse(access_token=token)


# --- Recherche OSINT ---

async def _run_search(query: str, query_type: str) -> None:
    """Tâche de fond : lance le moteur OSINT et enregistre l'historique."""
    from app.osint.engine import correlate
    from app.db.session import async_session

    try:
        results = await correlate(query, query_type)

        async with async_session() as session:
            entry = SearchHistory(
                query=query,
                query_type=query_type,
                result_count=len(results),
            )
            session.add(entry)
            await session.commit()

        logger.info(f"[search] Tâche terminée pour '{query}' ({query_type}) — {len(results)} résultat(s)")
    except Exception as e:
        logger.error(f"[search] Erreur pendant la recherche '{query}' : {e}")


@router.post("/api/search", response_model=SearchResponse, tags=["recherche"])
async def launch_search(
    body: SearchRequest,
    background_tasks: BackgroundTasks,
    token_data: dict = Depends(verify_token),
):
    user = token_data.get("sub", "unknown")
    search_rate_limiter.check(user)

    background_tasks.add_task(_run_search, body.query, body.query_type.value)
    logger.info(f"[search] Recherche lancée par {user} : '{body.query}' ({body.query_type.value})")

    return SearchResponse(
        message="Recherche lancée en arrière-plan",
        query=body.query,
        query_type=body.query_type.value,
    )


# --- Corrélations ---

@router.get("/api/correlations", response_model=list[CorrelationResponse], tags=["données"])
async def list_correlations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    result = await db.execute(
        select(Correlation).order_by(Correlation.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


# --- Personnes ---

@router.get("/api/persons", response_model=list[PersonResponse], tags=["données"])
async def list_persons(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    result = await db.execute(
        select(Person).order_by(Person.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


# --- Historique ---

@router.get("/api/history", response_model=list[SearchHistoryResponse], tags=["données"])
async def list_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    result = await db.execute(
        select(SearchHistory).order_by(SearchHistory.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()
