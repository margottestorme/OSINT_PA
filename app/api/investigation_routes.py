"""Routes de gestion des enquêtes (Module 14).

Une enquête (`Investigation`) regroupe des entités traçables (personnes, emails, sites,
corrélations, recherches) et sert de base à l'export d'un rapport PDF forensique. Chaque
action mutante est journalisée dans l'audit (`create_audit_log`) pour la traçabilité.

RBAC : lecture ouverte à tout utilisateur authentifié ; création/édition réservées aux
rôles admin/analyst ; suppression (archivage) réservée à l'admin.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.core.security import require_role, verify_token
from app.core.audit import create_audit_log, get_client_ip, resolve_user_id
from app.db.session import get_db
from app.db.models import (
    Correlation,
    Email,
    Investigation,
    InvestigationEntity,
    Person,
    SearchHistory,
    Site,
    UsernameSearch,
)
from app.schemas.schemas import (
    InvestigationCreate,
    InvestigationDetailResponse,
    InvestigationEntityCreate,
    InvestigationEntityResponse,
    InvestigationListItem,
    InvestigationResponse,
    InvestigationStatus,
    InvestigationUpdate,
    TimelineItem,
    TimelineResponse,
)

investigation_router = APIRouter(prefix="/api/investigations", tags=["enquêtes"])


async def _get_investigation_or_404(db: AsyncSession, investigation_id: int) -> Investigation:
    inv = await db.get(Investigation, investigation_id)
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enquête introuvable")
    return inv


# --- CRUD enquêtes ---

@investigation_router.post("", response_model=InvestigationResponse, status_code=status.HTTP_201_CREATED)
async def create_investigation(
    body: InvestigationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin", "analyst")),
):
    """Crée une enquête. L'analyste responsable est l'utilisateur courant."""
    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)

    inv = Investigation(
        title=body.title,
        description=body.description,
        status="open",
        analyst_id=user_id,
    )
    db.add(inv)
    await db.flush()

    await create_audit_log(
        db, "investigation_created",
        target=f"{inv.id}:{inv.title}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(f"[investigations] Enquête {inv.id} créée par {username}")
    return inv


@investigation_router.get("", response_model=list[InvestigationListItem])
async def list_investigations(
    status_filter: InvestigationStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Liste paginée des enquêtes, avec le nombre d'entités rattachées.

    Filtre optionnel par statut via `?status=open|closed|archived`.
    """
    stmt = select(Investigation)
    if status_filter is not None:
        stmt = stmt.where(Investigation.status == status_filter.value)
    stmt = stmt.order_by(Investigation.updated_at.desc()).limit(limit).offset(offset)

    investigations = list((await db.execute(stmt)).scalars().all())
    if not investigations:
        return []

    # Comptage groupé des entités en une seule requête (évite le N+1)
    ids = [i.id for i in investigations]
    count_rows = await db.execute(
        select(InvestigationEntity.investigation_id, func.count())
        .where(InvestigationEntity.investigation_id.in_(ids))
        .group_by(InvestigationEntity.investigation_id)
    )
    counts = {inv_id: n for inv_id, n in count_rows.all()}

    return [
        InvestigationListItem(
            **InvestigationResponse.model_validate(inv).model_dump(),
            entity_count=counts.get(inv.id, 0),
        )
        for inv in investigations
    ]


@investigation_router.get("/{investigation_id}", response_model=InvestigationDetailResponse)
async def get_investigation(
    investigation_id: int,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Détail d'une enquête et toutes ses entités rattachées (ordre de rattachement)."""
    inv = await _get_investigation_or_404(db, investigation_id)

    entities = list((await db.execute(
        select(InvestigationEntity)
        .where(InvestigationEntity.investigation_id == investigation_id)
        .order_by(InvestigationEntity.added_at, InvestigationEntity.id)
    )).scalars().all())

    return InvestigationDetailResponse(
        **InvestigationResponse.model_validate(inv).model_dump(),
        entities=[InvestigationEntityResponse.model_validate(e) for e in entities],
    )


@investigation_router.patch("/{investigation_id}", response_model=InvestigationResponse)
async def update_investigation(
    investigation_id: int,
    body: InvestigationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin", "analyst")),
):
    """Modifie le titre, la description et/ou le statut d'une enquête."""
    inv = await _get_investigation_or_404(db, investigation_id)

    updates = body.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] is not None:
        # Enum → valeur string stockée en base
        updates["status"] = InvestigationStatus(updates["status"]).value
    for field, value in updates.items():
        setattr(inv, field, value)
    await db.flush()

    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)
    await create_audit_log(
        db, "investigation_updated",
        target=f"{inv.id}:{list(updates.keys())}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(f"[investigations] Enquête {inv.id} mise à jour : {list(updates.keys())}")
    return inv


@investigation_router.delete("/{investigation_id}", response_model=InvestigationResponse)
async def delete_investigation(
    investigation_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin")),
):
    """Suppression logique (soft delete) : passe le statut à `archived`. Admin uniquement."""
    inv = await _get_investigation_or_404(db, investigation_id)

    inv.status = "archived"
    await db.flush()

    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)
    await create_audit_log(
        db, "investigation_archived",
        target=f"{inv.id}:{inv.title}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(f"[investigations] Enquête {inv.id} archivée par {username}")
    return inv


# --- Entités rattachées ---

@investigation_router.post(
    "/{investigation_id}/entities",
    response_model=InvestigationEntityResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_entity(
    investigation_id: int,
    body: InvestigationEntityCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin", "analyst")),
):
    """Rattache une entité (person/email/site/correlation/search) à l'enquête.

    Vérifie que l'enquête existe et que l'entité cible existe réellement (404 sinon).
    """
    inv = await _get_investigation_or_404(db, investigation_id)

    # Vérifie l'existence de l'entité cible dans sa table métier
    model = {
        "person": Person,
        "email": Email,
        "site": Site,
        "correlation": Correlation,
        "search": SearchHistory,
        "username_search": UsernameSearch,
    }[body.entity_type.value]
    target = await db.get(model, body.entity_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{body.entity_type.value} #{body.entity_id} introuvable",
        )

    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)

    link = InvestigationEntity(
        investigation_id=inv.id,
        entity_type=body.entity_type.value,
        entity_id=body.entity_id,
        added_by=user_id,
        notes=body.notes,
    )
    db.add(link)
    await db.flush()

    await create_audit_log(
        db, "investigation_entity_added",
        target=f"{inv.id}:{body.entity_type.value}#{body.entity_id}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(
        f"[investigations] {body.entity_type.value}#{body.entity_id} rattaché à l'enquête {inv.id}"
    )
    return link


@investigation_router.delete("/{investigation_id}/entities/{entity_id}", status_code=status.HTTP_200_OK)
async def remove_entity(
    investigation_id: int,
    entity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(require_role("admin", "analyst")),
):
    """Retire un rattachement de l'enquête.

    `entity_id` est l'identifiant de la ligne de rattachement (`InvestigationEntity.id`),
    et non l'id de l'entité métier — deux rattachements distincts peuvent viser la même
    entité, seul l'id du lien est sans ambiguïté.
    """
    await _get_investigation_or_404(db, investigation_id)

    link = await db.get(InvestigationEntity, entity_id)
    if not link or link.investigation_id != investigation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rattachement introuvable")

    await db.delete(link)
    await db.flush()

    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)
    await create_audit_log(
        db, "investigation_entity_removed",
        target=f"{investigation_id}:link#{entity_id}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(f"[investigations] Rattachement {entity_id} retiré de l'enquête {investigation_id}")
    return {"detail": "Rattachement retiré", "id": entity_id}


# --- Export PDF forensique ---

@investigation_router.get("/{investigation_id}/export/pdf")
async def export_investigation_pdf(
    investigation_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Génère et renvoie le rapport PDF forensique de l'enquête (JWT requis).

    Le PDF est construit **en mémoire** (aucune écriture disque). Un hash SHA256 du
    contenu est calculé avant la signature, imprimé sur la page d'intégrité et renvoyé
    aussi en en-tête `X-Content-SHA256`. `X-Report-UUID` porte le numéro de rapport.
    """
    from app.reports.pdf_generator import collect_report_data, build_investigation_pdf

    data = await collect_report_data(db, investigation_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enquête introuvable")

    pdf_bytes = build_investigation_pdf(data)

    username = token_data.get("sub", "unknown")
    user_id = await resolve_user_id(db, username)
    await create_audit_log(
        db, "investigation_report_exported",
        target=f"{investigation_id}:{data.report_uuid}",
        user_id=user_id, ip_address=get_client_ip(request),
    )
    logger.info(
        f"[investigations] Rapport PDF {data.report_uuid[:8]} exporté pour l'enquête "
        f"{investigation_id} par {username} (sha256={data.content_sha256[:12]}…)"
    )

    filename = f"rapport_enquete_{investigation_id}_{data.report_uuid[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": data.content_sha256,
            "X-Report-UUID": data.report_uuid,
            "X-Report-Generated-At": data.generated_at.isoformat(),
        },
    )


# --- Chronologie forensique ---

@investigation_router.get("/{investigation_id}/timeline", response_model=TimelineResponse)
async def get_timeline(
    investigation_id: int,
    db: AsyncSession = Depends(get_db),
    token_data: dict = Depends(verify_token),
):
    """Chronologie des entités de l'enquête, triée par date de découverte réelle.

    Chaque entité est datée par sa propre date de découverte (created_at / discovered_at
    de sa table métier), pas par sa date de rattachement — c'est une timeline forensique
    des faits, pas de la saisie. Les entités batch-résolues par type pour éviter le N+1.
    """
    await _get_investigation_or_404(db, investigation_id)

    links = list((await db.execute(
        select(InvestigationEntity)
        .where(InvestigationEntity.investigation_id == investigation_id)
    )).scalars().all())

    # Regroupe les ids par type pour une résolution par lot
    ids_by_type: dict[str, list[int]] = {}
    for link in links:
        ids_by_type.setdefault(link.entity_type, []).append(link.entity_id)

    # Résout (date de découverte, libellé) de chaque entité, par table métier
    resolved: dict[tuple[str, int], tuple] = {}  # (type, id) -> (discovered_at, label)

    async def _resolve(entity_type: str, model, date_attr: str, label_fn):
        ids = ids_by_type.get(entity_type)
        if not ids:
            return
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all()
        for row in rows:
            resolved[(entity_type, row.id)] = (getattr(row, date_attr), label_fn(row))

    await _resolve("person", Person, "created_at",
                   lambda p: f"{p.first_name} {p.last_name}".strip())
    await _resolve("email", Email, "created_at", lambda e: e.email)
    await _resolve("site", Site, "discovered_at", lambda s: s.url)
    await _resolve("correlation", Correlation, "created_at",
                   lambda c: f"Corrélation #{c.id} (score {c.score:.2f})")
    await _resolve("search", SearchHistory, "created_at",
                   lambda h: f"{h.query} ({h.query_type})")
    await _resolve("username_search", UsernameSearch, "created_at",
                   lambda u: f"@{u.username} ({u.found_count}/{u.total_platforms} trouvés)")

    items: list[TimelineItem] = []
    for link in links:
        discovered_at, label = resolved.get(
            (link.entity_type, link.entity_id),
            (None, f"{link.entity_type}#{link.entity_id} (supprimé)"),
        )
        items.append(TimelineItem(
            entity_type=link.entity_type,
            entity_id=link.entity_id,
            label=label,
            discovered_at=discovered_at,
            added_at=link.added_at,
            notes=link.notes,
        ))

    # Tri chronologique : entités datées d'abord (par date croissante), non datées à la fin
    items.sort(key=lambda it: (it.discovered_at is None, it.discovered_at or it.added_at))

    return TimelineResponse(
        investigation_id=investigation_id,
        total=len(items),
        items=items,
    )
