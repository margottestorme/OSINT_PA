from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.config import get_settings
from app.core.security import authenticate_user, create_access_token, require_role, verify_token
from app.core.audit import create_audit_log, get_client_ip, resolve_user_id
from app.db.session import get_db
from app.db.models import Correlation, Email, Person, SearchHistory, SearchResult, Site
from app.schemas.schemas import (
    CorrelationResponse,
    EmailResponse,
    HealthResponse,
    LoginRequest,
    PersonReportResponse,
    PersonResponse,
    PersonUpdate,
    SiteResponse,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
    StatsResponse,
    TokenResponse,
)
from app.api.rate_limit import login_rate_limiter, search_rate_limiter

router = APIRouter()


# --- Healthcheck (public) ---

@router.get("/health", response_model=HealthResponse, tags=["système"])
async def health_check():
    settings = get_settings()
    return HealthResponse(status="ok", version=settings.APP_VERSION)


# --- Auth ---

@router.post("/api/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ip = get_client_ip(request)
    # Throttle dédié anti-brute-force : 5 tentatives/minute par IP (S3)
    login_rate_limiter.check(ip)
    user = await authenticate_user(body.username, body.password, db)
    if not user:
        await create_audit_log(db, "login_failed", target=body.username, ip_address=ip)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides",
        )
    token = create_access_token({"sub": user.username, "role": user.role}, expires_minutes=15)
    await create_audit_log(db, "login_success", target=user.username, user_id=user.id, ip_address=ip)
    return TokenResponse(access_token=token)


# --- Recherche OSINT ---

async def _run_search(query: str, query_type: str) -> None:
    """Tâche de fond : lance le moteur OSINT, enregistre l'historique ET chaque résultat."""
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
            await session.flush()  # obtient entry.id pour rattacher les résultats

            # Persistance de chaque OsintResult (Approche B) — mapping 1:1
            for r in results:
                session.add(SearchResult(
                    search_id=entry.id,
                    source=r.get("source", "unknown"),
                    entity=r.get("entity", query),
                    entity_type=r.get("type", query_type),
                    data=r.get("data"),
                    extracted=r.get("extracted"),
                ))
            await session.commit()

        logger.info(f"[search] Tâche terminée pour '{query}' ({query_type}) — {len(results)} résultat(s) persistés")
    except Exception as e:
        logger.error(f"[search] Erreur pendant la recherche '{query}' : {e}")


@router.post("/api/search", response_model=SearchResponse, tags=["recherche"])
async def launch_search(
    body: SearchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    username = token_data.get("sub", "unknown")
    search_rate_limiter.check(username)

    user_id = await resolve_user_id(db, username)
    await create_audit_log(
        db, "search_launched",
        target=f"{body.query_type.value}:{body.query}",
        user_id=user_id, ip_address=get_client_ip(request),
    )

    background_tasks.add_task(_run_search, body.query, body.query_type.value)

    return SearchResponse(
        message="Recherche lancée en arrière-plan",
        query=body.query,
        query_type=body.query_type.value,
    )


# --- Corrélations ---

@router.get("/api/correlations", response_model=list[CorrelationResponse], tags=["données"])
async def list_correlations(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    result = await db.execute(
        select(Correlation).order_by(Correlation.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/api/emails", response_model=list[EmailResponse], tags=["données"])
async def list_emails(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    result = await db.execute(
        select(Email).order_by(Email.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/api/sites", response_model=list[SiteResponse], tags=["données"])
async def list_sites(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    result = await db.execute(
        select(Site).order_by(Site.discovered_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


# --- Personnes ---

@router.get("/api/persons", response_model=list[PersonResponse], tags=["données"])
async def list_persons(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    result = await db.execute(
        select(Person).order_by(Person.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.patch("/api/persons/{person_id}", response_model=PersonResponse, tags=["données"])
async def update_person(
    person_id: int,
    body: PersonUpdate,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Édition du nom/notes d'une personne (champs fournis uniquement)."""
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personne introuvable")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(person, field, value)
    await db.flush()
    logger.info(f"[persons] Personne {person_id} mise à jour : {list(updates.keys())}")
    return person


# --- Statistiques (KPI dashboard) ---

@router.get("/api/stats", response_model=StatsResponse, tags=["données"])
async def get_stats(
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Compteurs pour le dashboard : recherches, personnes, corrélations, dernière activité."""
    total_searches = await db.scalar(select(func.count()).select_from(SearchHistory))
    total_persons = await db.scalar(select(func.count()).select_from(Person))
    total_correlations = await db.scalar(select(func.count()).select_from(Correlation))
    last_activity = await db.scalar(select(func.max(SearchHistory.created_at)))

    return StatsResponse(
        total_searches=total_searches or 0,
        total_persons=total_persons or 0,
        total_correlations=total_correlations or 0,
        last_activity=last_activity,
    )


# --- Historique ---

@router.get("/api/history", response_model=list[SearchHistoryResponse], tags=["données"])
async def list_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    result = await db.execute(
        select(SearchHistory).order_by(SearchHistory.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/api/history/{search_id}", response_model=SearchHistoryResponse, tags=["données"])
async def get_search(
    search_id: int,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Métadonnées d'une recherche (requête, type, date, nombre de résultats)."""
    search = await db.get(SearchHistory, search_id)
    if not search:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recherche introuvable")
    return search


@router.get(
    "/api/history/{search_id}/results",
    response_model=list[SearchResultResponse],
    tags=["données"],
)
async def get_search_results(
    search_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Résultats détaillés d'une recherche manuelle (contenu réel, pas seulement le compteur)."""
    search = await db.get(SearchHistory, search_id)
    if not search:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recherche introuvable")

    result = await db.execute(
        select(SearchResult)
        .where(SearchResult.search_id == search_id)
        .order_by(SearchResult.id)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


# --- Report personne ---

@router.get("/api/persons/{person_id}/report", response_model=PersonReportResponse, tags=["données"])
async def get_person_report(
    person_id: int,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Personne introuvable")

    # Audit après vérification d'existence (m5) : on ne trace que les consultations réelles
    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)
    await create_audit_log(
        db, "report_viewed",
        target=str(person_id),
        user_id=user_id, ip_address=get_client_ip(request),
    )

    emails_result = await db.execute(
        select(Email).where(Email.person_id == person_id)
        .order_by(Email.created_at.desc()).limit(limit).offset(offset)
    )
    emails_list = list(emails_result.scalars().all())

    correlations_result = await db.execute(
        select(Correlation).where(Correlation.person_id == person_id)
        .order_by(Correlation.created_at.desc()).limit(limit).offset(offset)
    )
    correlations_list = correlations_result.scalars().all()

    site_ids = {c.site_id for c in correlations_list if c.site_id is not None}
    if site_ids:
        sites_result = await db.execute(
            select(Site).where(Site.id.in_(site_ids))
            .order_by(Site.discovered_at.desc()).limit(limit).offset(offset)
        )
        sites_list = sites_result.scalars().all()
    else:
        sites_list = []

    return PersonReportResponse(
        person=person,
        emails=emails_list,
        sites=sites_list,
        correlations=correlations_list,
    )
