import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.config import get_settings
from app.core.security import authenticate_user, create_access_token, require_role, verify_token
from app.core.audit import create_audit_log, get_client_ip, resolve_user_id
from app.db.session import get_db
from app.db.models import Correlation, Email, Person, SearchHistory, SearchResult, Site, hash_email
from app.schemas.schemas import (
    CorrelationResponse,
    EmailAddRequest,
    EmailResponse,
    EnrichResponse,
    FulltextResponse,
    FulltextScope,
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
    """Tâche de fond : lance le moteur OSINT, enregistre l'historique ET chaque résultat.

    Indexe ensuite chaque SearchResult dans Elasticsearch (best-effort : on est déjà
    dans une tâche de fond, donc non bloquant ; sans ES, l'indexation est un no-op).
    """
    from app.osint.engine import correlate
    from app.db.session import async_session
    from app.search.indexer import index_search_result

    try:
        results = await correlate(query, query_type)

        created: list[SearchResult] = []
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
                sr = SearchResult(
                    search_id=entry.id,
                    source=r.get("source", "unknown"),
                    entity=r.get("entity", query),
                    entity_type=r.get("type", query_type),
                    data=r.get("data"),
                    extracted=r.get("extracted"),
                )
                session.add(sr)
                created.append(sr)
            await session.commit()

        # Indexation ES après commit (expire_on_commit=False → attributs encore disponibles)
        for sr in created:
            await index_search_result(sr)

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


# --- Recherche full-text (Elasticsearch) ---

@router.get("/api/search/fulltext", response_model=FulltextResponse, tags=["recherche"])
async def fulltext_search_endpoint(
    q: str = Query("", description="Termes recherchés (full-text)"),
    scope: FulltextScope = Query(FulltextScope.all, alias="type"),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=500),
    token_data: dict = Depends(require_role("admin")),
):
    """Recherche full-text multi-index dans Elasticsearch (JWT admin requis).

    - `type` : persons / results / emails / sites / all (défaut : all)
    - Highlight des termes trouvés, tri par pertinence, pagination limit/offset.
    - Dégradation propre : si ES est indisponible, renvoie `{total: 0, hits: []}` (200).
    """
    if not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le paramètre 'q' ne peut pas être vide",
        )
    from app.search.fulltext import fulltext_search
    return await fulltext_search(q, scope.value, limit, offset)


@router.get("/api/search/status", tags=["recherche"])
async def fulltext_status(token_data: dict = Depends(require_role("admin"))):
    """Disponibilité d'Elasticsearch — permet à l'UI de distinguer « 0 résultat »
    d'un « ES indisponible » (dégradation propre)."""
    from app.search.elastic import elastic
    return {"available": await elastic.is_available()}


# --- Corrélations ---

@router.get("/api/correlations", response_model=list[CorrelationResponse], tags=["données"])
async def list_correlations(
    limit: int = Query(default=20, ge=1, le=500),
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
    limit: int = Query(default=20, ge=1, le=500),
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
    limit: int = Query(default=20, ge=1, le=500),
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
    limit: int = Query(default=20, ge=1, le=500),
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
    background_tasks: BackgroundTasks,
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

    # Réindexation ES en tâche de fond (non bloquant ; no-op si ES indisponible)
    from app.search.indexer import index_person
    background_tasks.add_task(index_person, person)

    logger.info(f"[persons] Personne {person_id} mise à jour : {list(updates.keys())}")
    return person


# Format email simple (suffisant pour valider une saisie manuelle, pas une vérification MX).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.post(
    "/api/persons/{person_id}/emails", response_model=EmailResponse,
    status_code=status.HTTP_201_CREATED, tags=["données"],
)
async def add_person_email(
    person_id: int,
    body: EmailAddRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Rattache manuellement un email à une personne — point de départ de l'enrichissement
    OSINT (`POST /api/persons/{person_id}/enrich`), qui pivote à partir des emails connus.

    Refusé sur une personne `source=lfw_dataset` (jeu de données académique, pas une vraie
    enquête — voir CLAUDE.md, section reconnaissance faciale).
    """
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Personne introuvable")
    if person.source == "lfw_dataset":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Impossible d'ajouter un email à une entrée du jeu de données académique LFW "
            "— ce n'est pas une vraie personne investiguée.",
        )

    email_addr = body.email.strip().lower()
    if not _EMAIL_RE.match(email_addr):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Format d'email invalide")

    existing = await db.execute(
        select(Email).where(Email.email_hash == hash_email(email_addr))
    )
    existing_email = existing.scalar_one_or_none()
    if existing_email:
        if existing_email.person_id == person_id:
            return existing_email  # idempotent
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cet email est déjà rattaché à une autre personne (id="
            f"{existing_email.person_id}).",
        )

    email_obj = Email(email=email_addr, source=body.source, person_id=person_id)
    db.add(email_obj)
    await db.flush()

    user_id = await resolve_user_id(db, token_data.get("sub", "unknown"))
    await create_audit_log(
        db, "email_added_manually", target=f"person_id={person_id}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(f"[persons] Email ajouté manuellement à person_id={person_id}")
    return email_obj


async def _run_enrichment(person_id: int) -> None:
    """Tâche de fond : ouvre sa propre session (celle de la requête est déjà fermée)."""
    from app.db.session import async_session
    from app.osint.correlator import enrich_person
    try:
        async with async_session() as session:
            result = await enrich_person(person_id, session)
            logger.info(f"[persons] Enrichissement manuel terminé : {result}")
    except Exception as e:
        logger.error(f"[persons] Erreur enrichissement manuel person_id={person_id} : {e}")


@router.post("/api/persons/{person_id}/enrich", response_model=EnrichResponse, tags=["données"])
async def enrich_person_endpoint(
    person_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Lance manuellement le pivot OSINT (email → WHOIS/DNS/sources → nouveaux emails/sites,
    jusqu'à 3 niveaux) sur une personne. Nécessite au moins un email déjà rattaché — voir
    `POST /api/persons/{person_id}/emails`. Tourne en arrière-plan (résultat visible ensuite
    sur le rapport/graphe), comme `POST /api/search`.
    """
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Personne introuvable")
    if person.source == "lfw_dataset":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Impossible d'enrichir une entrée du jeu de données académique LFW.",
        )

    has_email = await db.scalar(
        select(func.count()).select_from(Email).where(Email.person_id == person_id)
    )
    if not has_email:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Aucun email rattaché à cette personne — ajoutez-en un d'abord "
            "(POST /api/persons/{id}/emails) avant de lancer l'enrichissement.",
        )

    user_id = await resolve_user_id(db, token_data.get("sub", "unknown"))
    await create_audit_log(
        db, "enrichment_launched", target=f"person_id={person_id}",
        user_id=user_id, ip_address=get_client_ip(request),
    )

    background_tasks.add_task(_run_enrichment, person_id)

    return EnrichResponse(message="Enrichissement lancé en arrière-plan", person_id=person_id)


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
    limit: int = Query(default=20, ge=1, le=500),
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
